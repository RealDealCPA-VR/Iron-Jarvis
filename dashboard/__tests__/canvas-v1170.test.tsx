/**
 * v1.170.0 P6 — the workflow canvas grows up.
 *
 * Under test:
 *  1. ONE serializer — `stepsFromGraph` is used by both save() and run(); a
 *     def that never used `expect` must serialize byte-identically to the
 *     pre-v1.170.0 nine-field shape (the key is OMITTED, not null).
 *  2. DAG honesty — `connectionRefusal` refuses a second outgoing edge (the
 *     engine runs ONE chain; branches are Parallel groups on adjacent steps),
 *     and `splitGroupNodeIds` flags a group split by other steps in
 *     serialized order (it degrades to separate batches).
 *  3. ASK GATE — a `waiting` run's parked step is "waiting" (on you), never
 *     "running"; RunProgress renders the actual question with an inline
 *     answer box posting /workflows/runs/{id}/answer; 409 (answered from the
 *     chat card / bell first) surfaces honestly and never retries.
 *  4. Rename (contract 3) — `renameSavedDef` PATCHes /workflows/{name} and
 *     migrates the local layout; 404 = old row gone (plain save is correct);
 *     409 propagates.
 *  5. Pin preservation — `buildRunBody` carries the loaded def's project pin;
 *     the key is ABSENT (not "" — that would force-unpin) when there is none.
 *  6. NodeInspector "Prove it" — expect edits parse on blur; both lists empty
 *     collapses to expect: null so the serializer omits the key.
 *  7. Expect follows the KIND (reviewer defect) — `stepsFromGraph` serializes
 *     expect ONLY for agent/tool steps: an agent step given a `files` check
 *     and then switched to Notify used to keep an INVISIBLE expect (the
 *     inspector hides the section for ask/notify) that the engine fails on
 *     every run with no UI path to clear it.
 *  8. Pin lifecycle (reviewer defect) — rendered-component tests: "Save as
 *     new" forks an UNPINNED row so the canvas must drop the parent's pin
 *     (and invalidate an in-flight pin fetch); deleting the LOADED def clears
 *     loadedName/loadedPin so later runs are unpinned and a later save is a
 *     fresh create, never a rename PATCH against a deleted row.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";
import type { Connection, Edge, Node } from "@xyflow/react";

const { getMock, postMock, patchMock, delMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  patchMock: vi.fn(),
  delMock: vi.fn(),
}));

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
    patch: patchMock,
    del: delMock,
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
  appendDictation: (text: string, chunk: string) =>
    text ? `${text} ${chunk}` : chunk,
}));

// jsdom can't lay out the real React Flow canvas (ResizeObserver, DOM
// measurement); the pin-lifecycle tests drive the TOOLBAR around it, so stub
// only the renderer pieces and keep the store-free hooks (useNodesState,
// useEdgesState, addEdge) real.
vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  const { createElement } = await import("react");
  return {
    ...actual,
    ReactFlow: ({ children }: { children?: ReactNode }) =>
      createElement("div", { "data-testid": "rf-canvas" }, children),
    ReactFlowProvider: ({ children }: { children?: ReactNode }) =>
      createElement("div", null, children),
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    useReactFlow: () => ({ fitView: async () => false }),
  };
});

// next/link reaches for the App Router context, which does not exist in a
// bare jsdom render — collapse it to a plain anchor.
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({
      href,
      children,
      ...rest
    }: {
      href: string;
      children?: ReactNode;
    }) => createElement("a", { href, ...rest }, children),
  };
});

import { ApiError } from "@/lib/api";
import WorkflowCanvas, {
  buildGraph,
  buildRunBody,
  connectionRefusal,
  parseSteps,
  parseWaiting,
  renameSavedDef,
  RunProgress,
  runStepViews,
  splitGroupNodeIds,
  stepsFromGraph,
  type CanvasStep,
} from "@/components/workflow/WorkflowCanvas";
import { NodeInspector } from "@/components/workflow/NodeInspector";
import type { StepNodeData } from "@/components/workflow/agents";
import type { WorkflowRun } from "@/lib/types";

/* ---- graph fixtures ------------------------------------------------------- */

const TRIGGER: Node = {
  id: "trigger",
  type: "trigger",
  position: { x: 40, y: 168 },
  data: { label: "Manual run" },
};

function stepNode(id: string, x: number, data: Partial<StepNodeData> = {}): Node {
  return {
    id,
    type: "step",
    position: { x, y: 148 },
    data: { name: id, agent: "builder", task: "", tool: null, ...data },
  };
}

const edge = (source: string, target: string): Edge => ({
  id: `${source}->${target}`,
  source,
  target,
});

/** Trigger → a → b → c chain. */
function chain(...steps: Node[]): { nodes: Node[]; edges: Edge[] } {
  const nodes = [TRIGGER, ...steps];
  const edges: Edge[] = [];
  let prev = "trigger";
  for (const s of steps) {
    edges.push(edge(prev, s.id));
    prev = s.id;
  }
  return { nodes, edges };
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  patchMock.mockReset();
  delMock.mockReset();
  localStorage.clear();
});

afterEach(() => {
  cleanup();
});

/* ---- 1. ONE serializer ---------------------------------------------------- */

describe("stepsFromGraph (the one serializer)", () => {
  it("serializes a legacy step to EXACTLY the pre-v1.170.0 nine fields — no expect key", () => {
    const { nodes, edges } = chain(
      stepNode("s1", 320, { name: "Gather", agent: "planner", task: "  find it  " }),
    );
    const steps = stepsFromGraph(nodes, edges);
    expect(steps).toHaveLength(1);
    const s = steps[0] as unknown as Record<string, unknown>;
    expect(Object.keys(s).sort()).toEqual([
      "agent",
      "args",
      "group",
      "kind",
      "message",
      "name",
      "on_failure",
      "task",
      "tool",
    ]);
    // Mutation guards: the exact old defaults, task trimmed, no expect at all.
    expect(s).toEqual({
      name: "Gather",
      agent: "planner",
      task: "find it",
      tool: null,
      kind: "agent",
      on_failure: "halt",
      group: null,
      args: {},
      message: "",
    });
    expect("expect" in s).toBe(false);
  });

  it("names an unnamed step by its 1-based position", () => {
    const { nodes, edges } = chain(stepNode("s1", 320, { name: "  " }));
    expect(stepsFromGraph(nodes, edges)[0].name).toBe("step-1");
  });

  it("orders by the edge chain, not node insertion order", () => {
    // c sits leftmost by x but LAST in the chain — edges win.
    const a = stepNode("a", 900, { name: "First" });
    const b = stepNode("b", 600, { name: "Second" });
    const c = stepNode("c", 100, { name: "Third" });
    const nodes = [TRIGGER, c, b, a];
    const edges = [edge("trigger", "a"), edge("a", "b"), edge("b", "c")];
    expect(stepsFromGraph(nodes, edges).map((s) => s.name)).toEqual([
      "First",
      "Second",
      "Third",
    ]);
  });

  it("emits expect ONLY when a check is non-empty, and omits the empty half", () => {
    const { nodes, edges } = chain(
      stepNode("s1", 320, {
        expect: { files: ["reports/summary.md"], summary_contains: [] },
      }),
    );
    const s = stepsFromGraph(nodes, edges)[0];
    expect(s.expect).toEqual({ files: ["reports/summary.md"] });
    expect("summary_contains" in (s.expect as object)).toBe(false);
  });

  it("drops whitespace-only expect entries and then omits the key entirely", () => {
    const { nodes, edges } = chain(
      stepNode("s1", 320, { expect: { files: ["  ", ""], summary_contains: [] } }),
    );
    expect("expect" in (stepsFromGraph(nodes, edges)[0] as object)).toBe(false);
  });

  it("round-trips a saved def with v1.121 fields AND expect through load → serialize", () => {
    const saved: CanvasStep[] = [
      {
        name: "Fetch",
        agent: "researcher",
        task: "pull the folder",
        tool: null,
        kind: "agent",
        on_failure: "retry",
        group: "batch",
        args: {},
        message: "",
        expect: { files: ["out/a.csv"], summary_contains: ["rows"] },
      },
      {
        name: "Notify",
        agent: "builder",
        task: "",
        tool: null,
        kind: "notify",
        on_failure: "skip",
        group: null,
        args: { channel: "email" },
        message: "done: {{Fetch}}",
      },
    ];
    const parsed = parseSteps(JSON.stringify(saved));
    const { nodes, edges } = buildGraph(parsed);
    expect(stepsFromGraph(nodes, edges)).toEqual(saved);
  });

  /* Reviewer defect: expect must follow the KIND. The inspector only shows
     (and can only clear) the "Prove it" section for agent/tool steps, and the
     engine rejects `files` checks on ask/notify — so serializing a hidden
     expect after a kind switch made the step fail EVERY run with nothing
     visible in the editor. */

  it.each(["ask", "notify"] as const)(
    "NEVER serializes expect for a %s step — the inspector can't show it and the engine would fail every run",
    (kind) => {
      const { nodes, edges } = chain(
        stepNode("s1", 320, {
          kind,
          message: "check in",
          expect: { files: ["out/report.md"], summary_contains: ["done"] },
        }),
      );
      const s = stepsFromGraph(nodes, edges)[0] as unknown as Record<string, unknown>;
      expect(s.kind).toBe(kind);
      expect("expect" in s).toBe(false);
    },
  );

  it("a kind switch agent → notify drops the expect from the serialized step; switching back restores it", () => {
    const checks = { files: ["out/a.md"], summary_contains: ["ok"] };
    const { nodes, edges } = chain(stepNode("s1", 320, { expect: checks }));
    // As an agent step (the default kind) the checks serialize…
    expect(stepsFromGraph(nodes, edges)[0].expect).toEqual(checks);
    // …after the inspector's kind picker flips it to notify they must NOT…
    const switched = nodes.map((n) =>
      n.id === "s1" ? { ...n, data: { ...n.data, kind: "notify" } } : n,
    );
    expect("expect" in (stepsFromGraph(switched, edges)[0] as object)).toBe(false);
    // …and flipping back to agent restores them (node data was untouched).
    const restored = switched.map((n) =>
      n.id === "s1" ? { ...n, data: { ...n.data, kind: "agent" } } : n,
    );
    expect(stepsFromGraph(restored, edges)[0].expect).toEqual(checks);
  });

  it("tool steps DO keep their expect — the engine checks created_paths/data for tools", () => {
    const { nodes, edges } = chain(
      stepNode("s1", 320, {
        kind: "tool",
        tool: "write_file",
        expect: { files: ["out/x.csv"] },
      }),
    );
    expect(stepsFromGraph(nodes, edges)[0].expect).toEqual({
      files: ["out/x.csv"],
    });
  });
});

/* ---- 2. DAG honesty ------------------------------------------------------- */

describe("connectionRefusal", () => {
  const conn = (source: string, target: string): Connection =>
    ({ source, target, sourceHandle: null, targetHandle: null }) as Connection;

  it("allows the first outgoing edge", () => {
    const { edges } = chain(stepNode("a", 320), stepNode("b", 600));
    expect(connectionRefusal(edges, conn("b", "c"))).toBeNull();
  });

  it("refuses a SECOND outgoing edge and explains Parallel groups", () => {
    const { edges } = chain(stepNode("a", 320), stepNode("b", 600));
    const refusal = connectionRefusal(edges, conn("a", "c"));
    expect(refusal).toBeTruthy();
    expect(refusal).toContain("Parallel group");
    expect(refusal).toContain("adjacent");
  });

  it("does not refuse re-drawing the SAME edge (addEdge dedupes it)", () => {
    const { edges } = chain(stepNode("a", 320), stepNode("b", 600));
    expect(connectionRefusal(edges, conn("a", "b"))).toBeNull();
  });

  it("refuses a self-loop", () => {
    expect(connectionRefusal([], conn("a", "a"))).toBeTruthy();
  });

  it("refuses trigger fan-out too — the engine runs one chain from the trigger", () => {
    const { edges } = chain(stepNode("a", 320));
    expect(connectionRefusal(edges, conn("trigger", "b"))).toBeTruthy();
  });
});

describe("splitGroupNodeIds", () => {
  it("adjacent same-group steps are NOT flagged", () => {
    const { nodes, edges } = chain(
      stepNode("a", 320, { group: "g" }),
      stepNode("b", 600, { group: "g" }),
      stepNode("c", 880),
    );
    expect(splitGroupNodeIds(nodes, edges).size).toBe(0);
  });

  it("a group split by another step flags BOTH members, not the interloper", () => {
    const { nodes, edges } = chain(
      stepNode("a", 320, { group: "g" }),
      stepNode("b", 600),
      stepNode("c", 880, { group: "g" }),
    );
    const split = splitGroupNodeIds(nodes, edges);
    expect(split).toEqual(new Set(["a", "c"]));
  });

  it("a single-member group is never flagged", () => {
    const { nodes, edges } = chain(
      stepNode("a", 320, { group: "solo" }),
      stepNode("b", 600),
    );
    expect(splitGroupNodeIds(nodes, edges).size).toBe(0);
  });

  it("ungrouped steps are ignored entirely", () => {
    const { nodes, edges } = chain(stepNode("a", 320), stepNode("b", 600));
    expect(splitGroupNodeIds(nodes, edges).size).toBe(0);
  });
});

/* ---- 3. ask gate ---------------------------------------------------------- */

const WAITING_RUN: WorkflowRun = {
  id: "wfrun-1",
  workflow_name: "monthly-close",
  status: "waiting",
  steps_json: JSON.stringify([
    { name: "Gather", agent: "planner", task: "t" },
    { name: "Confirm", kind: "ask", message: "Send it?" },
    { name: "Send", agent: "builder", task: "send" },
  ]),
  outputs_json: JSON.stringify({
    Gather: { status: "completed", summary: "gathered" },
  }),
  waiting_json: JSON.stringify({
    index: 1,
    step: "Confirm",
    question: "Send the summary email?",
  }),
};

describe("parseWaiting + runStepViews", () => {
  it("a running run has no waiting ask and one 'running' step", () => {
    const run = { ...WAITING_RUN, status: "running", waiting_json: "" };
    expect(parseWaiting(run)).toBeNull();
    const views = runStepViews(run);
    expect(views.map((v) => v.status)).toEqual(["completed", "running", "pending"]);
  });

  it("the parked step is 'waiting' — NEVER 'running' — and later steps stay pending", () => {
    const views = runStepViews(WAITING_RUN);
    expect(views.map((v) => v.status)).toEqual(["completed", "waiting", "pending"]);
    expect(views.some((v) => v.status === "running")).toBe(false);
  });

  it("corrupt waiting_json still parks honestly: generic question, first open step waits", () => {
    const run = { ...WAITING_RUN, waiting_json: "not json {" };
    const ask = parseWaiting(run);
    expect(ask?.question).toBe("This run needs your answer.");
    const views = runStepViews(run);
    expect(views.map((v) => v.status)).toEqual(["completed", "waiting", "pending"]);
  });

  it("terminal statuses assign NO live step (interrupted → open steps pending)", () => {
    const run = { ...WAITING_RUN, status: "interrupted", waiting_json: "" };
    const views = runStepViews(run);
    expect(views.map((v) => v.status)).toEqual(["completed", "pending", "pending"]);
  });

  it("parseWaiting surfaces one-tap options when present", () => {
    const run = {
      ...WAITING_RUN,
      waiting_json: JSON.stringify({
        index: 1,
        step: "Confirm",
        question: "Approve?",
        options: ["approve", "reject"],
      }),
    };
    expect(parseWaiting(run)?.options).toEqual(["approve", "reject"]);
  });
});

describe("RunProgress ask gate", () => {
  const noop = () => {};

  it("renders 'waiting on you' (not the raw status), the question, and an answer box", () => {
    render(
      <RunProgress run={WAITING_RUN} onCancel={noop} cancelling={false} />,
    );
    expect(screen.getAllByText(/waiting on you/i).length).toBeGreaterThan(0);
    // The raw slate "waiting" badge must not appear anywhere.
    expect(screen.queryByText(/^waiting$/)).toBeNull();
    const gate = screen.getByTestId("run-ask-gate");
    expect(gate.textContent).toContain("Send the summary email?");
    expect(screen.getByRole("button", { name: "Answer" })).toBeDisabled();
  });

  it("posts the TRIMMED answer to the encoded answer route and reports the new status up", async () => {
    postMock.mockResolvedValue({ id: "wfrun-1", status: "running", answered: true });
    const onAnswered = vi.fn();
    render(
      <RunProgress
        run={{ ...WAITING_RUN, id: "wfrun-a/b" }}
        onCancel={noop}
        cancelling={false}
        onAnswered={onAnswered}
      />,
    );
    fireEvent.change(screen.getByLabelText("Answer workflow monthly-close"), {
      target: { value: "  yes, send it  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/workflows/runs/wfrun-a%2Fb/answer", {
        answer: "yes, send it",
      }),
    );
    await waitFor(() => expect(onAnswered).toHaveBeenCalledWith("running"));
    expect(postMock).toHaveBeenCalledTimes(1);
  });

  it("Enter in the input submits", async () => {
    postMock.mockResolvedValue({ status: "running" });
    render(
      <RunProgress run={WAITING_RUN} onCancel={noop} cancelling={false} />,
    );
    const input = screen.getByLabelText("Answer workflow monthly-close");
    fireEvent.change(input, { target: { value: "option B" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/workflows/runs/wfrun-1/answer", {
        answer: "option B",
      }),
    );
  });

  it("renders one-tap option buttons that answer directly", async () => {
    postMock.mockResolvedValue({ status: "running" });
    const run = {
      ...WAITING_RUN,
      waiting_json: JSON.stringify({
        index: 1,
        step: "Confirm",
        question: "Approve the draft?",
        options: ["approve", "reject"],
      }),
    };
    render(<RunProgress run={run} onCancel={noop} cancelling={false} />);
    fireEvent.click(screen.getByRole("button", { name: "approve" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/workflows/runs/wfrun-1/answer", {
        answer: "approve",
      }),
    );
  });

  it("409 (answered elsewhere): honest note with the server detail, gate leaves, NO retry", async () => {
    postMock.mockRejectedValue(
      new ApiError("run is running, not waiting — it may already be answered", 409),
    );
    render(
      <RunProgress run={WAITING_RUN} onCancel={noop} cancelling={false} />,
    );
    fireEvent.change(screen.getByLabelText("Answer workflow monthly-close"), {
      target: { value: "yes" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));
    const note = await screen.findByTestId("run-ask-conflict");
    expect(note.textContent).toContain("Already answered elsewhere");
    expect(note.textContent).toContain(
      "run is running, not waiting — it may already be answered",
    );
    expect(screen.queryByTestId("run-ask-gate")).toBeNull();
    expect(postMock).toHaveBeenCalledTimes(1);
  });

  it("a non-409 failure keeps the gate answerable with the error inline", async () => {
    postMock.mockRejectedValue(new ApiError("daemon offline", 0));
    render(
      <RunProgress run={WAITING_RUN} onCancel={noop} cancelling={false} />,
    );
    fireEvent.change(screen.getByLabelText("Answer workflow monthly-close"), {
      target: { value: "yes" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));
    await screen.findByText("daemon offline");
    expect(screen.getByTestId("run-ask-gate")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Answer" })).not.toBeDisabled();
  });

  it("a resuming run shows a real 'resuming' badge, no ask gate, and Cancel stays (still live)", () => {
    const run = { ...WAITING_RUN, status: "resuming" };
    render(<RunProgress run={run} onCancel={noop} cancelling={false} />);
    expect(screen.getByText("resuming")).toBeInTheDocument();
    expect(screen.queryByTestId("run-ask-gate")).toBeNull();
    expect(screen.getByRole("button", { name: /Cancel/ })).toBeInTheDocument();
  });

  it("a completed run renders neither Cancel nor the gate", () => {
    const run = { ...WAITING_RUN, status: "completed", waiting_json: "" };
    render(<RunProgress run={run} onCancel={noop} cancelling={false} />);
    expect(screen.queryByRole("button", { name: /Cancel/ })).toBeNull();
    expect(screen.queryByTestId("run-ask-gate")).toBeNull();
  });
});

/* ---- 4. rename via PATCH (contract 3) ------------------------------------- */

describe("renameSavedDef", () => {
  it("PATCHes the encoded old name with {new_name} and migrates the saved layout", async () => {
    patchMock.mockResolvedValue({ name: "new name" });
    localStorage.setItem("ij.wf.layout.old wf", '{"s1":{"x":1,"y":2}}');
    const renamed = await renameSavedDef("old wf", "new name");
    expect(renamed).toBe(true);
    expect(patchMock).toHaveBeenCalledWith("/workflows/old%20wf", {
      new_name: "new name",
    });
    expect(localStorage.getItem("ij.wf.layout.new name")).toBe(
      '{"s1":{"x":1,"y":2}}',
    );
    expect(localStorage.getItem("ij.wf.layout.old wf")).toBeNull();
  });

  it("404 (old row gone) returns false without throwing — plain save is then correct", async () => {
    patchMock.mockRejectedValue(new ApiError("no such workflow", 404));
    await expect(renameSavedDef("gone", "new")).resolves.toBe(false);
  });

  it("409 (name taken) propagates for honest surfacing", async () => {
    patchMock.mockRejectedValue(new ApiError("workflow “taken” already exists", 409));
    await expect(renameSavedDef("a", "taken")).rejects.toMatchObject({
      status: 409,
    });
    // The layout must NOT have been touched on a failed rename.
    expect(localStorage.getItem("ij.wf.layout.taken")).toBeNull();
  });
});

/* ---- 5. run body pin preservation ----------------------------------------- */

describe("buildRunBody", () => {
  const steps: CanvasStep[] = [{ name: "s", agent: "builder", task: "" }];

  it("carries the loaded def's pin explicitly", () => {
    expect(buildRunBody("wf", steps, "proj-7")).toEqual({
      name: "wf",
      steps,
      project_id: "proj-7",
    });
  });

  it("OMITS the key when there is no pin — '' would force-unpin, null is not the same as absent", () => {
    const body = buildRunBody("wf", steps, null);
    expect("project_id" in body).toBe(false);
    expect(body).toEqual({ name: "wf", steps });
  });
});

/* ---- 6. NodeInspector "Prove it" ------------------------------------------ */

describe("NodeInspector expect section", () => {
  const base: StepNodeData = {
    name: "Gather",
    agent: "builder",
    task: "do it",
    kind: "agent",
  };

  function renderInspector(data: StepNodeData) {
    const onChange = vi.fn();
    render(
      <NodeInspector
        data={data}
        onChange={onChange}
        onDelete={() => {}}
        onClose={() => {}}
      />,
    );
    return onChange;
  }

  it("agent steps get the section; blur parses one file per line, dropping blanks", () => {
    const onChange = renderInspector(base);
    expect(screen.getByTestId("expect-section")).toBeInTheDocument();
    const files = screen.getByLabelText("Files this step must produce");
    fireEvent.blur(files, {
      target: { value: "reports/x.md\n\n   \nout/y.csv  " },
    });
    expect(onChange).toHaveBeenCalledWith({
      expect: { files: ["reports/x.md", "out/y.csv"] },
    });
  });

  it("summary phrases land under summary_contains without an empty files key", () => {
    const onChange = renderInspector(base);
    fireEvent.blur(screen.getByLabelText("Phrases the step summary must contain"), {
      target: { value: "filed\nreviewed" },
    });
    const patch = onChange.mock.calls[0][0] as { expect: object };
    expect(patch.expect).toEqual({ summary_contains: ["filed", "reviewed"] });
    expect("files" in patch.expect).toBe(false);
  });

  it("clearing the last check collapses to expect: null so the key is never serialized", () => {
    const onChange = renderInspector({
      ...base,
      expect: { files: ["old.md"] },
    });
    fireEvent.blur(screen.getByLabelText("Files this step must produce"), {
      target: { value: "   " },
    });
    expect(onChange).toHaveBeenCalledWith({ expect: null });
  });

  it("tool steps get the section too; ask steps do not", () => {
    renderInspector({ ...base, kind: "tool", tool: "list_folder" });
    expect(screen.getByTestId("expect-section")).toBeInTheDocument();
    cleanup();
    renderInspector({ ...base, kind: "ask", message: "ok?" });
    expect(screen.queryByTestId("expect-section")).toBeNull();
  });
});

/* ---- 8. pin lifecycle (rendered component) --------------------------------- */

describe("Canvas pin lifecycle (Save as new / delete)", () => {
  const PARENT_STEPS = [{ name: "A", agent: "builder", task: "t" }];
  const RUN_DONE = {
    id: "r1",
    workflow_name: "parent",
    status: "completed",
    steps_json: JSON.stringify(PARENT_STEPS),
    outputs_json: "{}",
  };

  /** Load a saved def onto the canvas the way the workflows page does. When
   *  `project_id` is left undefined, loadDef fires the detail-route pin fetch
   *  (the list endpoint omits the pin). */
  async function loadParent(project_id?: string | null) {
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("ij:load-workflow", {
          detail: {
            id: 1,
            name: "parent",
            description: "",
            steps_json: JSON.stringify(PARENT_STEPS),
            ...(project_id === undefined ? {} : { project_id }),
          },
        }),
      );
    });
    await screen.findByDisplayValue("parent");
  }

  /** Click Run and return the LAST /workflows/run body posted. */
  async function clickRun() {
    const before = postMock.mock.calls.filter((c) => c[0] === "/workflows/run").length;
    fireEvent.click(screen.getByRole("button", { name: /Run workflow/ }));
    await waitFor(() =>
      expect(
        postMock.mock.calls.filter((c) => c[0] === "/workflows/run").length,
      ).toBe(before + 1),
    );
    const calls = postMock.mock.calls.filter((c) => c[0] === "/workflows/run");
    return calls[calls.length - 1][1] as {
      name: string;
      steps: unknown[];
      project_id?: string;
    };
  }

  it("Save as new drops the parent's pin — the fork runs UNPINNED, like every other surface running the same saved def", async () => {
    getMock.mockImplementation(async () => ({ workflows: [] }));
    postMock.mockImplementation(async (path: string) =>
      path === "/workflows/run" ? RUN_DONE : {},
    );
    render(<WorkflowCanvas />);
    await loadParent("proj-1");
    // Sanity (guards the fork assertion against a never-set pin): a run of
    // the loaded def carries its pin explicitly.
    expect((await clickRun()).project_id).toBe("proj-1");
    // Fork it under a new name.
    fireEvent.change(screen.getByLabelText("Workflow name"), {
      target: { value: "fork-wf" },
    });
    fireEvent.click(await screen.findByRole("button", { name: /Save as new/ }));
    // WAIT FOR THE HANDLER, NOT FOR THE POST (fixed after a CI failure on
    // v1.178.0; the same shape flaked in v1.177.1). `save()` awaits
    // `post("/workflows")` and only AFTERWARDS runs `setLoadedPin(null)` —
    // so the POST being recorded is true one render BEFORE the fork is
    // actually unpinned, and clicking Run in that window sends the PARENT's
    // project_id. Green on a fast machine, red on a contended runner.
    // The success note is set at the END of the handler, so seeing it means
    // the pin has been cleared and rendered.
    expect(
      await screen.findByText(/Saved .*fork-wf/),
    ).toBeInTheDocument();
    expect(postMock.mock.calls.some((c) => c[0] === "/workflows")).toBe(true);
    expect(patchMock).not.toHaveBeenCalled(); // as-new never renames
    // POST /workflows saved the fork UNPINNED (a fresh name has no pin row to
    // preserve) — the canvas must agree or its runs ground sessions in the
    // PARENT's project while chat/schedules run the fork unpinned.
    const body = await clickRun();
    expect(body.name).toBe("fork-wf");
    expect("project_id" in body).toBe(false);
  });

  it("an in-flight pin fetch for the parent cannot re-pin the fork after Save as new", async () => {
    let resolvePin!: (v: { project_id?: string | null }) => void;
    getMock.mockImplementation((path: string) => {
      if (path === "/workflows/parent")
        return new Promise((res) => {
          resolvePin = res;
        });
      return Promise.resolve({ workflows: [] });
    });
    postMock.mockImplementation(async (path: string) =>
      path === "/workflows/run" ? RUN_DONE : {},
    );
    render(<WorkflowCanvas />);
    // The list row omitted project_id → the detail fetch is now in flight.
    await loadParent(undefined);
    fireEvent.change(screen.getByLabelText("Workflow name"), {
      target: { value: "fork-wf" },
    });
    fireEvent.click(await screen.findByRole("button", { name: /Save as new/ }));
    // Same reason as the test above: wait for the HANDLER to finish, not for
    // its POST. `pinFetchRef.current = wfName` — the stale-response guard this
    // test is about — is set in the same block as `setLoadedPin(null)`, AFTER
    // the awaited post. Resolving the late pin before that ran would arm the
    // guard too late and the test would measure nothing.
    expect(
      await screen.findByText(/Saved .*fork-wf/),
    ).toBeInTheDocument();
    // The parent's pin arrives LATE — the stale-response guard must refuse it.
    await act(async () => {
      resolvePin({ project_id: "proj-1" });
    });
    expect("project_id" in (await clickRun())).toBe(false);
  });

  it("deleting the LOADED def clears name+pin: the next run is unpinned and a later save is a fresh create, never a rename PATCH", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    try {
      getMock.mockImplementation(async () => ({
        workflows: [
          {
            id: 1,
            name: "parent",
            description: "",
            steps_json: JSON.stringify(PARENT_STEPS),
          },
        ],
      }));
      postMock.mockImplementation(async (path: string) =>
        path === "/workflows/run" ? RUN_DONE : {},
      );
      delMock.mockResolvedValue({});
      render(<WorkflowCanvas />);
      await loadParent("proj-1");
      // Sanity: the pin was live before the delete.
      expect((await clickRun()).project_id).toBe("proj-1");
      // Delete the loaded row from the Load ▾ dropdown.
      fireEvent.click(screen.getByRole("button", { name: "Load" }));
      fireEvent.click(await screen.findByRole("button", { name: "Delete parent" }));
      await waitFor(() =>
        expect(delMock).toHaveBeenCalledWith("/workflows/parent"),
      );
      await screen.findByText(/Deleted/);
      // The row is gone: a run must NOT carry the deleted def's pin…
      expect("project_id" in (await clickRun())).toBe(false);
      // …and a save under a new name is a plain create — "Save as new" (the
      // fork affordance) is gone and no rename PATCH targets the deleted row.
      fireEvent.change(screen.getByLabelText("Workflow name"), {
        target: { value: "reborn" },
      });
      expect(screen.queryByRole("button", { name: /Save as new/ })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() =>
        expect(postMock.mock.calls.some((c) => c[0] === "/workflows")).toBe(true),
      );
      expect(patchMock).not.toHaveBeenCalled();
    } finally {
      confirmSpy.mockRestore();
    }
  });
});

/* ------------------------- v1.222.0: the page's Saved list deletes a row */

import { WORKFLOWS_LIST_EVENT } from "@/components/workflow/SavedWorkflows";

describe("Canvas hears a delete from the page's Saved-workflows list (v1.222.0)", () => {
  const PARENT_STEPS = [{ name: "A", agent: "builder", task: "t" }];
  const RUN_DONE = {
    id: "r1",
    workflow_name: "parent",
    status: "completed",
    steps_json: JSON.stringify(PARENT_STEPS),
    outputs_json: "{}",
  };

  async function lastRunBody() {
    const before = postMock.mock.calls.filter((c) => c[0] === "/workflows/run").length;
    fireEvent.click(screen.getByRole("button", { name: /Run workflow/ }));
    await waitFor(() =>
      expect(
        postMock.mock.calls.filter((c) => c[0] === "/workflows/run").length,
      ).toBe(before + 1),
    );
    const calls = postMock.mock.calls.filter((c) => c[0] === "/workflows/run");
    return calls[calls.length - 1][1] as { project_id?: string };
  }

  it("forgets the loaded def: no pin on Run, a plain create on Save, list refreshed", async () => {
    getMock.mockImplementation(async () => ({ workflows: [] }));
    postMock.mockImplementation(async (path: string) =>
      path === "/workflows/run" ? RUN_DONE : {},
    );
    render(<WorkflowCanvas />);
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("ij:load-workflow", {
          detail: {
            id: 1,
            name: "parent",
            description: "",
            steps_json: JSON.stringify(PARENT_STEPS),
            project_id: "proj-1",
          },
        }),
      );
    });
    await screen.findByDisplayValue("parent");
    expect((await lastRunBody()).project_id).toBe("proj-1");
    const listCalls = () => getMock.mock.calls.filter((c) => c[0] === "/workflows").length;
    const before = listCalls();

    // The page's list deleted "parent" — no click on this canvas at all.
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(WORKFLOWS_LIST_EVENT, { detail: { deleted: "parent" } }),
      );
    });

    await waitFor(() => expect(listCalls()).toBeGreaterThan(before));
    expect("project_id" in (await lastRunBody())).toBe(false);
    fireEvent.change(screen.getByLabelText("Workflow name"), {
      target: { value: "reborn" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(postMock.mock.calls.some((c) => c[0] === "/workflows")).toBe(true),
    );
    expect(patchMock).not.toHaveBeenCalled();
  });
});
