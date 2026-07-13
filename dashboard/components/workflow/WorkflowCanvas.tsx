"use client";

// n8n-style visual workflow editor built on React Flow (@xyflow/react).
//
// A Trigger start node feeds a left-to-right chain of Step nodes (each step =
// {name, agent, task}). Edges are animated for the "moving pieces" feel. The
// toolbar adds/edits/deletes steps and runs the workflow against the daemon's
// POST /workflows/run, serializing nodes in topological order.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  MarkerType,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type Connection,
  type DefaultEdgeOptions,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import Link from "next/link";
import {
  Workflow,
  Play,
  Plus,
  CircleCheck,
  CircleX,
  Circle,
  MinusCircle,
  Loader2,
  Ban,
  Trash2,
  ChevronRight,
  FolderOpen,
  ChevronDown,
  Save,
  RefreshCw,
  CalendarClock,
} from "lucide-react";
import { get, post, del, ApiError } from "@/lib/api";
import type { WorkflowRun } from "@/lib/types";
import {
  Badge,
  OfflineHint,
  ErrorNote,
  SuccessNote,
  LoaderInline,
} from "@/components/ui";
import { StepNode } from "./StepNode";
import { TriggerNode } from "./TriggerNode";
import { NodeInspector } from "./NodeInspector";
import {
  agentMeta,
  type StepNodeData,
  type WorkflowDef,
} from "./agents";

/* nodeTypes / edge defaults must be stable references (defined at module scope). */
const nodeTypes = { trigger: TriggerNode, step: StepNode };

const defaultEdgeOptions: DefaultEdgeOptions = {
  animated: true,
  style: { stroke: "#22d3ee", strokeWidth: 2 },
  markerEnd: { type: MarkerType.ArrowClosed, color: "#22d3ee", width: 18, height: 18 },
};

/* ---- Seed: Trigger → Gather → Draft → Review ----------------------------- */

function mkStep(
  id: string,
  name: string,
  agent: string,
  task: string,
  x: number,
  y: number,
  tool?: string | null,
): Node {
  return {
    id,
    type: "step",
    position: { x, y },
    data: { name, agent, task, tool: tool ?? null },
  };
}
function mkEdge(source: string, target: string): Edge {
  return { id: `${source}->${target}`, source, target, animated: true };
}

const SEED_NODES: Node[] = [
  {
    id: "trigger",
    type: "trigger",
    position: { x: 40, y: 168 },
    data: { label: "Manual run" },
    deletable: false,
  },
  mkStep("s1", "Gather", "planner", "Gather the context and requirements needed for the task.", 320, 148),
  mkStep("s2", "Draft", "builder", "Draft an initial implementation from the gathered context.", 600, 148),
  mkStep("s3", "Review", "reviewer", "Review the draft for correctness and quality; flag any fixes.", 880, 148),
];
const SEED_EDGES: Edge[] = [mkEdge("trigger", "s1"), mkEdge("s1", "s2"), mkEdge("s2", "s3")];

/* ---- Rebuild a node graph from saved steps (Load) ------------------------ */

const STEP_X0 = 320; // first step x (matches the seed layout)
const STEP_DX = 280; // left-to-right spacing
const STEP_Y = 148;

interface RawStep {
  name?: string;
  agent?: string;
  task?: string;
  tool?: string | null;
}

/** Turn a saved `[{name,agent,task}]` list into a Trigger → step₁ → … chain
 *  laid out left-to-right, mirroring the seed graph's geometry. */
function buildGraph(steps: RawStep[]): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [
    {
      id: "trigger",
      type: "trigger",
      position: { x: 40, y: 168 },
      data: { label: "Manual run" },
      deletable: false,
    },
  ];
  const edges: Edge[] = [];
  let prev = "trigger";
  steps.forEach((s, i) => {
    const id = `s${i + 1}`;
    // Preserve the saved agent verbatim (built-in OR dynamic) — never coerce an
    // unknown agent to "builder"; the inspector renders it as-is.
    const agent = String(s.agent || "builder");
    nodes.push(
      mkStep(
        id,
        s.name?.trim() || `Step ${i + 1}`,
        agent,
        s.task ?? "",
        STEP_X0 + i * STEP_DX,
        STEP_Y,
        s.tool ?? null,
      ),
    );
    edges.push(mkEdge(prev, id));
    prev = id;
  });
  return { nodes, edges };
}

/** Parse a `steps_json` string into a RawStep[] (tolerant of bad data). */
function parseSteps(stepsJson: string | undefined | null): RawStep[] {
  try {
    const parsed = JSON.parse(stepsJson || "[]");
    return Array.isArray(parsed) ? (parsed as RawStep[]) : [];
  } catch {
    return [];
  }
}

/* ---- Topological (left-to-right) ordering -------------------------------- */

function topoOrder(nodes: Node[], edges: Edge[]): Node[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const indeg = new Map(nodes.map((n) => [n.id, 0]));
  const adj = new Map<string, string[]>(nodes.map((n) => [n.id, []]));
  for (const e of edges) {
    if (!adj.has(e.source) || !indeg.has(e.target)) continue;
    adj.get(e.source)!.push(e.target);
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
  }
  const byX = (a: Node, b: Node) => a.position.x - b.position.x;
  const queue = nodes
    .filter((n) => (indeg.get(n.id) ?? 0) === 0)
    .sort(byX)
    .map((n) => n.id);
  const seen = new Set<string>();
  const out: Node[] = [];
  while (queue.length) {
    const id = queue.shift()!;
    if (seen.has(id)) continue;
    seen.add(id);
    const node = byId.get(id);
    if (node) out.push(node);
    const nexts = (adj.get(id) ?? [])
      .map((t) => byId.get(t))
      .filter((n): n is Node => !!n)
      .sort(byX);
    for (const nx of nexts) {
      indeg.set(nx.id, (indeg.get(nx.id) ?? 1) - 1);
      if ((indeg.get(nx.id) ?? 0) <= 0) queue.push(nx.id);
    }
  }
  // Append anything stranded by a cycle, ordered by x.
  for (const n of [...nodes].sort(byX)) if (!seen.has(n.id)) out.push(n);
  return out;
}

const orderedSteps = (nodes: Node[], edges: Edge[]) =>
  topoOrder(nodes, edges).filter((n) => n.type === "step");

/* ---- Layout persistence (node positions per workflow name) --------------- */

const layoutKey = (name: string) => `ij.wf.layout.${name}`;

/** Persist each node's position so a reload/Load doesn't reset a hand-tuned
 *  layout back to the auto left-to-right chain. */
function saveLayout(name: string, nodes: Node[]) {
  if (!name) return;
  try {
    const pos: Record<string, { x: number; y: number }> = {};
    for (const n of nodes) pos[n.id] = { x: n.position.x, y: n.position.y };
    localStorage.setItem(layoutKey(name), JSON.stringify(pos));
  } catch {
    /* ignore (private mode / quota) */
  }
}

function loadLayout(name: string): Record<string, { x: number; y: number }> | null {
  try {
    const raw = localStorage.getItem(layoutKey(name));
    if (!raw) return null;
    const p = JSON.parse(raw);
    return p && typeof p === "object" && !Array.isArray(p) ? p : null;
  } catch {
    return null;
  }
}

/** Overlay saved positions onto a freshly-built graph (ids are deterministic:
 *  trigger, s1, s2, …), leaving edges — rebuilt from step order — untouched. */
function applyLayout(
  nodes: Node[],
  layout: Record<string, { x: number; y: number }> | null,
): Node[] {
  if (!layout) return nodes;
  return nodes.map((n) => {
    const p = layout[n.id];
    return p && typeof p.x === "number" && typeof p.y === "number"
      ? { ...n, position: { x: p.x, y: p.y } }
      : n;
  });
}

/* ---- Live run: derive per-step chips from a run record ------------------- */

const RUN_TERMINAL = new Set([
  "completed",
  "failed",
  "cancelled",
  "interrupted",
  "error",
]);

interface StepOutput {
  session_id?: string | null;
  status?: string;
  summary?: string;
  tool?: string | null;
}

interface RunStepView {
  name: string;
  agent?: string;
  status: string;
  summary?: string;
  session_id?: string | null;
}

/** Parse an `outputs_json` object string into a stepName → output map. */
function parseOutputs(json: unknown): Record<string, StepOutput> {
  try {
    const p = JSON.parse(String(json ?? "{}"));
    return p && typeof p === "object" && !Array.isArray(p) ? p : {};
  } catch {
    return {};
  }
}

/** Merge the ordered steps_json with the live outputs_json into one view list.
 *  While the run is active, the first step lacking an output entry is the one
 *  currently "running"; later un-entered steps are "pending". */
function runStepViews(run: WorkflowRun): RunStepView[] {
  const steps = parseSteps((run as { steps_json?: string }).steps_json);
  const outputs = parseOutputs((run as { outputs_json?: string }).outputs_json);
  const active = !RUN_TERMINAL.has(String(run.status ?? ""));
  let runningAssigned = false;
  return steps.map((s, i) => {
    const nm = s.name?.trim() || `step-${i + 1}`;
    const out = outputs[nm];
    let status: string;
    if (out?.status) status = out.status;
    else if (active && !runningAssigned) {
      status = "running";
      runningAssigned = true;
    } else status = "pending";
    return {
      name: nm,
      agent: s.agent,
      status,
      summary: out?.summary,
      session_id: out?.session_id,
    };
  });
}

const CHIP_TONE: Record<string, string> = {
  completed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  running: "border-accent/40 bg-accent/10 text-accent-soft animate-pulse",
  failed: "border-rose-500/30 bg-rose-500/10 text-rose-300",
  skipped: "border-white/[0.08] bg-white/[0.02] text-zinc-500",
  pending: "border-white/[0.08] bg-white/[0.02] text-zinc-500",
};

function ChipIcon({ status }: { status: string }) {
  if (status === "completed") return <CircleCheck size={12} />;
  if (status === "failed") return <CircleX size={12} />;
  if (status === "running") return <Loader2 size={12} className="animate-spin" />;
  if (status === "skipped") return <MinusCircle size={12} />;
  return <Circle size={12} />;
}

/** The live run strip: a status header + cancel, a chip per step, and an
 *  honest collapsible per-step results panel (summaries; failures in red). */
function RunProgress({
  run,
  onCancel,
  cancelling,
}: {
  run: WorkflowRun;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const steps = runStepViews(run);
  const status = String(run.status ?? "running");
  const active = !RUN_TERMINAL.has(status);
  const [open, setOpen] = useState<string | null>(null);
  const hasResults = steps.some((s) => s.summary || s.status === "failed");

  return (
    <div className="space-y-2.5 rounded-xl border border-white/[0.08] bg-ink-950/40 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge value={status} />
        <span className="text-zinc-300">
          Run <b className="font-semibold text-zinc-100">{run.workflow_name}</b>
        </span>
        {active && <LoaderInline />}
        {active && (
          <button
            type="button"
            onClick={onCancel}
            disabled={cancelling}
            className="ml-auto flex items-center gap-1.5 rounded-lg border border-rose-500/25 bg-rose-500/[0.07] px-2.5 py-1 text-xs font-medium text-rose-200 transition-colors hover:border-rose-500/50 hover:bg-rose-500/[0.12] disabled:opacity-50"
          >
            <Ban size={13} /> {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        )}
      </div>

      {/* One chip per step */}
      <div className="flex flex-wrap items-center gap-1.5">
        {steps.map((s) => (
          <span
            key={s.name}
            title={`${s.name} — ${s.status}`}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium ${
              CHIP_TONE[s.status] ?? CHIP_TONE.pending
            }`}
          >
            <ChipIcon status={s.status} />
            <span className="max-w-[140px] truncate">{s.name}</span>
          </span>
        ))}
      </div>

      {/* Honest per-step results (collapsible summaries; failures in red) */}
      {hasResults && (
        <div className="space-y-1">
          {steps
            .filter((s) => s.summary || s.status === "failed")
            .map((s) => {
              const failed = s.status === "failed";
              const isOpen = open === s.name;
              return (
                <div
                  key={s.name}
                  className={`rounded-lg border ${
                    failed
                      ? "border-rose-500/25 bg-rose-500/[0.05]"
                      : "border-white/[0.06] bg-white/[0.02]"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => setOpen(isOpen ? null : s.name)}
                    className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12px]"
                  >
                    <ChevronRight
                      size={13}
                      className={`shrink-0 text-zinc-500 transition-transform ${
                        isOpen ? "rotate-90" : ""
                      }`}
                    />
                    <span
                      className={`font-medium ${failed ? "text-rose-200" : "text-zinc-200"}`}
                    >
                      {s.name}
                    </span>
                    <Badge value={s.status} />
                  </button>
                  {isOpen && (
                    <p
                      className={`whitespace-pre-wrap px-3 pb-2.5 pt-0.5 text-[12px] leading-relaxed ${
                        failed ? "text-rose-200/90" : "text-zinc-400"
                      }`}
                    >
                      {s.summary || (failed ? "Step failed." : "No summary.")}
                    </p>
                  )}
                </div>
              );
            })}
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */

interface RunResult {
  offline?: boolean;
}

function Canvas() {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(SEED_NODES);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(SEED_EDGES);
  const [name, setName] = useState("demo-workflow");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  /* Live run: the polled record + a Cancel-in-flight flag + the poll handle. */
  const [activeRun, setActiveRun] = useState<WorkflowRun | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);
  // Stop the poll loop if the editor unmounts mid-run.
  useEffect(() => stopPolling, [stopPolling]);

  /* Saved/agent-authored workflow defs for the Load ▾ dropdown. */
  const [defs, setDefs] = useState<WorkflowDef[]>([]);
  const [defsLoading, setDefsLoading] = useState(false);
  const [loadOpen, setLoadOpen] = useState(false);
  const loadRef = useRef<HTMLDivElement | null>(null);

  const idRef = useRef(4);
  const { fitView } = useReactFlow();

  /* Keep each step card's 1-based index in sync with graph order. Re-runs only
     when the edge set or node count changes — not on every data edit. */
  useEffect(() => {
    setNodes((nds) => {
      const order = orderedSteps(nds, edges).map((n) => n.id);
      const indexById = new Map(order.map((id, i) => [id, i + 1]));
      let changed = false;
      const next = nds.map((n) => {
        if (n.type !== "step") return n;
        const idx = indexById.get(n.id);
        if ((n.data as StepNodeData).index === idx) return n;
        changed = true;
        return { ...n, data: { ...n.data, index: idx } };
      });
      return changed ? next : nds;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [edges, nodes.length, setNodes]);

  const onConnect = useCallback(
    (c: Connection) => setEdges((eds) => addEdge({ ...c, animated: true }, eds)),
    [setEdges],
  );

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => setSelectedId(node.type === "step" ? node.id : null),
    [],
  );
  const onPaneClick = useCallback(() => setSelectedId(null), []);

  const addStep = useCallback(() => {
    const order = topoOrder(nodes, edges);
    const last = order[order.length - 1] ?? nodes.find((n) => n.id === "trigger")!;
    const id = `step-${idRef.current++}`;
    const stepCount = nodes.filter((n) => n.type === "step").length;
    const newNode = mkStep(
      id,
      `Step ${stepCount + 1}`,
      "builder",
      "",
      last.position.x + 280,
      last.type === "trigger" ? last.position.y - 20 : last.position.y,
    );
    setNodes((nds) => [...nds, newNode]);
    setEdges((eds) => addEdge(mkEdge(last.id, id), eds));
    setSelectedId(id);
    setTimeout(() => fitView({ padding: 0.22, duration: 420 }), 60);
  }, [nodes, edges, setNodes, setEdges, fitView]);

  const updateData = useCallback(
    (id: string, patch: Partial<StepNodeData>) =>
      setNodes((nds) =>
        nds.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)),
      ),
    [setNodes],
  );

  const deleteNode = useCallback(
    (id: string) => {
      if (id === "trigger") return;
      const preds = edges.filter((e) => e.target === id).map((e) => e.source);
      const succs = edges.filter((e) => e.source === id).map((e) => e.target);
      const rewires: Edge[] = [];
      for (const p of preds)
        for (const s of succs) if (p !== s) rewires.push(mkEdge(p, s));
      setEdges((eds) => {
        let next = eds.filter((e) => e.source !== id && e.target !== id);
        for (const r of rewires)
          if (!next.some((e) => e.source === r.source && e.target === r.target))
            next = [...next, r];
        return next;
      });
      setNodes((nds) => nds.filter((n) => n.id !== id));
      setSelectedId((cur) => (cur === id ? null : cur));
    },
    [edges, setEdges, setNodes],
  );

  const onNodesDelete = useCallback(
    (deleted: Node[]) =>
      setSelectedId((cur) => (deleted.some((n) => n.id === cur) ? null : cur)),
    [],
  );

  /* ---- Load: list saved defs, rebuild a graph from one ------------------- */

  const refreshDefs = useCallback(async () => {
    setDefsLoading(true);
    try {
      const res = await get<{ workflows: WorkflowDef[] }>("/workflows");
      setDefs(Array.isArray(res.workflows) ? res.workflows : []);
    } catch {
      // Offline/error: leave the list empty — the dropdown shows the hint and
      // a Save/Run attempt surfaces the OfflineHint.
      setDefs([]);
    } finally {
      setDefsLoading(false);
    }
  }, []);

  // Populate the Load list on mount so agent-authored workflows are there.
  useEffect(() => {
    refreshDefs();
  }, [refreshDefs]);

  // Bridge: the "Build with chat" panel (workflows/page.tsx) dispatches this
  // event with a generated {name, description, steps_json} workflow, and the
  // terminal "→ Workflow" handoff dispatches {name, description, steps: [...]}
  // — accept both shapes and load via the SAME path as the Load dropdown, then
  // refresh the saved list (the workflow was persisted server-side).
  useEffect(() => {
    type LoadDetail = Omit<WorkflowDef, "steps_json"> & {
      steps_json?: string;
      steps?: unknown[];
    };
    const onLoad = (e: Event) => {
      const def = (e as CustomEvent).detail as LoadDetail | undefined;
      if (!def) return;
      const steps_json =
        typeof def.steps_json === "string"
          ? def.steps_json
          : Array.isArray(def.steps)
            ? JSON.stringify(def.steps)
            : undefined;
      if (typeof steps_json !== "string") return;
      loadDef({ ...def, steps_json });
      refreshDefs();
    };
    window.addEventListener("ij:load-workflow", onLoad);
    return () => window.removeEventListener("ij:load-workflow", onLoad);
    // loadDef is stable (useCallback); refreshDefs too.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Close the Load dropdown on an outside click.
  useEffect(() => {
    if (!loadOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (loadRef.current && !loadRef.current.contains(e.target as HTMLElement))
        setLoadOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [loadOpen]);

  const loadDef = useCallback(
    (def: WorkflowDef) => {
      const steps = parseSteps(def.steps_json);
      const { nodes: nn, edges: ee } = buildGraph(steps);
      // Restore a hand-tuned layout (positions saved under this name) if present.
      idRef.current = steps.length + 1;
      setNodes(applyLayout(nn, loadLayout(def.name)));
      setEdges(ee);
      setName(def.name);
      setSelectedId(null);
      setLoadOpen(false);
      setResult(null);
      setActiveRun(null);
      setError(null);
      setSuccess(
        `Loaded “${def.name}” — ${steps.length} step${steps.length === 1 ? "" : "s"}.`,
      );
      // Tell the "Build with chat" panel what's loaded so a follow-up refines
      // THIS workflow instead of minting a context-free new one.
      try {
        window.dispatchEvent(
          new CustomEvent("ij:workflow-changed", {
            detail: { name: def.name, steps },
          }),
        );
      } catch {
        /* ignore */
      }
      setTimeout(() => fitView({ padding: 0.22, duration: 480 }), 80);
    },
    [setNodes, setEdges, fitView],
  );

  /* ---- Save: serialize the graph and upsert it server-side --------------- */

  const save = useCallback(async () => {
    setError(null);
    setSuccess(null);
    setResult(null);
    const steps = orderedSteps(nodes, edges).map((n, i) => {
      const d = n.data as StepNodeData;
      return {
        name: d.name?.trim() || `step-${i + 1}`,
        agent: d.agent,
        task: (d.task ?? "").trim(),
        tool: d.tool ?? null,
      };
    });
    const wfName = name.trim();
    if (!wfName) {
      setError("Name the workflow before saving.");
      return;
    }
    if (steps.length === 0) {
      setError("Add at least one step before saving.");
      return;
    }
    setSaving(true);
    try {
      await post("/workflows", {
        name: wfName,
        steps,
        description: "saved from the workflow editor",
      });
      // Persist the current node layout so a later Load restores it verbatim.
      saveLayout(wfName, nodes);
      setSuccess(
        `Saved “${wfName}” — ${steps.length} step${steps.length === 1 ? "" : "s"}. It’s in the Load list.`,
      );
      try {
        window.dispatchEvent(
          new CustomEvent("ij:workflow-changed", {
            detail: { name: wfName, steps },
          }),
        );
      } catch {
        /* ignore */
      }
      await refreshDefs();
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setResult({ offline: true });
      else setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [nodes, edges, name, refreshDefs]);

  const run = useCallback(async () => {
    setError(null);
    setResult(null);
    setSuccess(null);
    setActiveRun(null);
    setCancelling(false);
    stopPolling();
    const steps = orderedSteps(nodes, edges).map((n, i) => {
      const d = n.data as StepNodeData;
      return {
        name: d.name?.trim() || `step-${i + 1}`,
        agent: d.agent,
        task: (d.task ?? "").trim(),
        tool: d.tool ?? null,
      };
    });
    const wfName = name.trim() || "demo-workflow";
    if (steps.length === 0) {
      setError("Add at least one step before running.");
      return;
    }
    setBusy(true);
    try {
      // POST returns the freshly-created record AT ONCE (status "running"); the
      // engine runs the steps in the background. Poll for progress every 2s.
      const rec = await post<WorkflowRun>("/workflows/run", { name: wfName, steps });
      setActiveRun(rec);
      const runId = rec.id ? String(rec.id) : "";
      if (!runId || RUN_TERMINAL.has(String(rec.status ?? ""))) {
        setBusy(false);
        return;
      }
      pollRef.current = setInterval(async () => {
        try {
          const fresh = await get<WorkflowRun>(
            `/workflows/runs/${encodeURIComponent(runId)}`,
          );
          setActiveRun(fresh);
          if (RUN_TERMINAL.has(String(fresh.status ?? ""))) {
            stopPolling();
            setBusy(false);
            setCancelling(false);
          }
        } catch {
          // Transient fetch error (daemon busy/restarting) — keep polling.
        }
      }, 2000);
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setResult({ offline: true });
      else setError(err instanceof ApiError ? err.message : String(err));
      setBusy(false);
    }
  }, [nodes, edges, name, stopPolling]);

  const cancelRun = useCallback(async () => {
    const id = activeRun?.id ? String(activeRun.id) : "";
    if (!id) return;
    setCancelling(true);
    try {
      const res = await post<{ id: string; status: string }>(
        `/workflows/runs/${encodeURIComponent(id)}/cancel`,
      );
      // Reflect "cancelling" immediately; the poll loop lands the final state.
      setActiveRun((r) => (r ? { ...r, status: res.status } : r));
    } catch (err) {
      // 409 = the run already finished; surface it and stop the spinner.
      setCancelling(false);
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, [activeRun]);

  /* ---- Delete a saved workflow from the Load list ------------------------ */

  const deleteDef = useCallback(
    async (defName: string) => {
      if (
        typeof window !== "undefined" &&
        !window.confirm(`Delete workflow “${defName}”? This can't be undone.`)
      )
        return;
      try {
        await del(`/workflows/${encodeURIComponent(defName)}`);
        try {
          localStorage.removeItem(layoutKey(defName));
        } catch {
          /* ignore */
        }
        setSuccess(`Deleted “${defName}”.`);
        await refreshDefs();
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) await refreshDefs();
        else setError(err instanceof ApiError ? err.message : String(err));
      }
    },
    [refreshDefs],
  );

  const selected = nodes.find((n) => n.id === selectedId && n.type === "step");
  const selData = selected?.data as StepNodeData | undefined;
  const stepCount = nodes.filter((n) => n.type === "step").length;

  const miniColor = useCallback((node: Node) => {
    if (node.type === "trigger") return "#22d3ee";
    return agentMeta(String((node.data as StepNodeData).agent)).hex;
  }, []);

  return (
    <div className="card-surface flex h-[calc(100vh-12.5rem)] min-h-[560px] flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 border-b hairline px-4 py-3">
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-accent/30 bg-accent/10 text-accent-soft">
            <Workflow size={16} />
          </span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="workflow name"
            aria-label="Workflow name"
            className="min-w-0 max-w-[280px] flex-1 rounded-lg border border-transparent bg-transparent px-1.5 py-1 text-sm font-semibold text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 hover:border-white/10 focus:border-accent/50 focus:bg-ink-900/60"
          />
          <span className="hidden rounded-full border border-white/[0.07] bg-white/[0.03] px-2 py-0.5 text-[11px] text-zinc-500 sm:inline">
            {stepCount} step{stepCount === 1 ? "" : "s"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {/* Load ▾ — saved & agent-authored workflows */}
          <div ref={loadRef} className="relative">
            <button
              type="button"
              onClick={() => {
                setLoadOpen((o) => {
                  if (!o) refreshDefs();
                  return !o;
                });
              }}
              aria-haspopup="listbox"
              aria-expanded={loadOpen}
              className="btn-ghost"
            >
              <FolderOpen size={15} /> Load
              <ChevronDown
                size={14}
                className={`transition-transform ${loadOpen ? "rotate-180" : ""}`}
              />
            </button>

            {loadOpen && (
              <div className="card-surface absolute right-0 top-[calc(100%+8px)] z-30 w-72 origin-top-right overflow-hidden">
                <div className="flex items-center justify-between gap-2 border-b hairline px-3 py-2">
                  <span className="text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    {defsLoading
                      ? "Loading…"
                      : defs.length
                        ? `Loaded ${defs.length} workflow${defs.length === 1 ? "" : "s"}`
                        : "Saved workflows"}
                  </span>
                  <button
                    type="button"
                    onClick={() => refreshDefs()}
                    aria-label="Refresh list"
                    className="rounded-md border border-white/10 p-1 text-zinc-500 transition-colors hover:border-white/20 hover:text-zinc-200"
                  >
                    <RefreshCw
                      size={12}
                      className={defsLoading ? "animate-spin-slow" : ""}
                    />
                  </button>
                </div>
                <div className="max-h-72 overflow-y-auto p-1.5">
                  {defs.length === 0 && !defsLoading && (
                    <div className="px-2.5 py-6 text-center text-xs text-zinc-500">
                      No saved workflows yet. Workflows you save — or that agents
                      author — show up here.
                    </div>
                  )}
                  {defs.map((d) => {
                    const n = parseSteps(d.steps_json).length;
                    return (
                      <div
                        key={d.id ?? d.name}
                        className="group flex items-center gap-1 rounded-lg pr-1 transition-colors hover:bg-white/[0.05]"
                      >
                        <button
                          type="button"
                          onClick={() => loadDef(d)}
                          className="flex min-w-0 flex-1 items-start gap-2.5 rounded-lg px-2.5 py-2 text-left"
                        >
                          <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md border border-accent/30 bg-accent/10 text-accent-soft">
                            <Workflow size={13} />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[13px] font-medium text-zinc-100 group-hover:text-white">
                              {d.name}
                            </span>
                            <span className="block truncate text-[11px] text-zinc-500">
                              {n} step{n === 1 ? "" : "s"}
                              {d.description ? ` · ${d.description}` : ""}
                            </span>
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => deleteDef(d.name)}
                          aria-label={`Delete ${d.name}`}
                          title={`Delete “${d.name}”`}
                          className="shrink-0 rounded-md p-1.5 text-zinc-600 opacity-0 transition-all hover:bg-rose-500/10 hover:text-rose-300 focus-visible:opacity-100 group-hover:opacity-100"
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="btn-ghost"
          >
            {saving ? <LoaderInline label="Saving…" /> : (<><Save size={15} /> Save</>)}
          </button>
          <Link
            href={`/schedules?workflow=${encodeURIComponent(name)}`}
            title="Run this workflow on a schedule"
            className="btn-ghost"
          >
            <CalendarClock size={15} /> Schedule…
          </Link>
          <button type="button" onClick={addStep} className="btn-ghost">
            <Plus size={15} /> Add step
          </button>
          <button type="button" onClick={run} disabled={busy} className="btn-accent">
            {busy ? <LoaderInline label="Running…" /> : (<><Play size={14} /> Run workflow</>)}
          </button>
        </div>
      </div>

      {/* Canvas */}
      <div className="relative flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          onNodesDelete={onNodesDelete}
          nodeTypes={nodeTypes}
          defaultEdgeOptions={defaultEdgeOptions}
          colorMode="dark"
          fitView
          fitViewOptions={{ padding: 0.25 }}
          minZoom={0.3}
          maxZoom={1.75}
          className="!bg-transparent"
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={22}
            size={1}
            color="rgba(148,163,184,0.14)"
          />
          <Controls
            showInteractive={false}
            className="!rounded-xl !border !border-white/[0.07] !shadow-card"
          />
          <MiniMap
            pannable
            zoomable
            nodeStrokeWidth={2}
            nodeColor={miniColor}
            maskColor="rgba(7,8,9,0.72)"
            className="!rounded-xl !border !border-white/[0.07]"
            style={{ backgroundColor: "rgba(11,13,17,0.92)" }}
          />
        </ReactFlow>

        {selData && (
          <NodeInspector
            data={selData}
            onChange={(patch) => updateData(selected!.id, patch)}
            onDelete={() => deleteNode(selected!.id)}
            onClose={() => setSelectedId(null)}
          />
        )}
      </div>

      {/* Result strip */}
      {(activeRun || result || error || success) && (
        <div className="space-y-2 border-t hairline p-3">
          {result?.offline && (
            <OfflineHint detail="couldn't reach the daemon for this workflow." />
          )}
          {success && !error && <SuccessNote>{success}</SuccessNote>}
          {activeRun && (
            <RunProgress
              run={activeRun}
              onCancel={cancelRun}
              cancelling={cancelling}
            />
          )}
          {error && <ErrorNote>{error}</ErrorNote>}
        </div>
      )}
    </div>
  );
}

export default function WorkflowCanvas() {
  // ReactFlowProvider gives us useReactFlow() (fitView) inside <Canvas/>.
  return (
    <ReactFlowProvider>
      <Canvas />
    </ReactFlowProvider>
  );
}
