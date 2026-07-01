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

import {
  Workflow,
  Play,
  Plus,
  CircleCheck,
  FolderOpen,
  ChevronDown,
  Save,
  RefreshCw,
} from "lucide-react";
import { get, post, ApiError } from "@/lib/api";
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
  AGENT_TYPES,
  type AgentType,
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
  agent: AgentType,
  task: string,
  x: number,
  y: number,
): Node {
  return { id, type: "step", position: { x, y }, data: { name, agent, task } };
}
function mkEdge(source: string, target: string): Edge {
  return { id: `${source}->${target}`, source, target, animated: true };
}

const SEED_NODES: Node[] = [
  {
    id: "trigger",
    type: "trigger",
    position: { x: 40, y: 168 },
    data: { label: "Trigger" },
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
}

/** Turn a saved `[{name,agent,task}]` list into a Trigger → step₁ → … chain
 *  laid out left-to-right, mirroring the seed graph's geometry. */
function buildGraph(steps: RawStep[]): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [
    {
      id: "trigger",
      type: "trigger",
      position: { x: 40, y: 168 },
      data: { label: "Trigger" },
      deletable: false,
    },
  ];
  const edges: Edge[] = [];
  let prev = "trigger";
  steps.forEach((s, i) => {
    const id = `s${i + 1}`;
    const agent: AgentType = (AGENT_TYPES as string[]).includes(String(s.agent))
      ? (s.agent as AgentType)
      : "builder";
    nodes.push(
      mkStep(
        id,
        s.name?.trim() || `Step ${i + 1}`,
        agent,
        s.task ?? "",
        STEP_X0 + i * STEP_DX,
        STEP_Y,
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

/* -------------------------------------------------------------------------- */

interface RunResult {
  offline?: boolean;
  name?: string;
  status?: string;
  sessions?: number;
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
      idRef.current = steps.length + 1;
      setNodes(nn);
      setEdges(ee);
      setName(def.name);
      setSelectedId(null);
      setLoadOpen(false);
      setResult(null);
      setError(null);
      setSuccess(
        `Loaded “${def.name}” — ${steps.length} step${steps.length === 1 ? "" : "s"}.`,
      );
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
      setSuccess(
        `Saved “${wfName}” — ${steps.length} step${steps.length === 1 ? "" : "s"}. It’s in the Load list.`,
      );
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
    const steps = orderedSteps(nodes, edges).map((n, i) => {
      const d = n.data as StepNodeData;
      return {
        name: d.name?.trim() || `step-${i + 1}`,
        agent: d.agent,
        task: (d.task ?? "").trim(),
      };
    });
    const wfName = name.trim() || "demo-workflow";
    if (steps.length === 0) {
      setError("Add at least one step before running.");
      return;
    }
    setBusy(true);
    try {
      const rec = await post<WorkflowRun>("/workflows/run", { name: wfName, steps });
      let sessions = 0;
      try {
        const ids = JSON.parse(String(rec.session_ids_json ?? "[]"));
        sessions = Array.isArray(ids) ? ids.length : 0;
      } catch {
        /* ignore */
      }
      setResult({
        name: rec.workflow_name ?? wfName,
        status: rec.status ?? "done",
        sessions,
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setResult({ offline: true });
      else setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [nodes, edges, name]);

  const selected = nodes.find((n) => n.id === selectedId && n.type === "step");
  const selData = selected?.data as StepNodeData | undefined;
  const stepCount = nodes.filter((n) => n.type === "step").length;

  const miniColor = useCallback((node: Node) => {
    if (node.type === "trigger") return "#22d3ee";
    return agentMeta(String((node.data as StepNodeData).agent)).hex;
  }, []);

  const goodRun =
    !!result && !result.offline &&
    /^(completed|ok|succeeded|success)$/i.test(result.status ?? "");

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
                      <button
                        key={d.id ?? d.name}
                        type="button"
                        onClick={() => loadDef(d)}
                        className="group flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-white/[0.05]"
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
            color="rgba(34,211,238,0.16)"
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
      {(result || error || success) && (
        <div className="border-t hairline p-3">
          {result?.offline && (
            <OfflineHint detail="couldn't reach the daemon for this workflow." />
          )}
          {success && !error && <SuccessNote>{success}</SuccessNote>}
          {result && !result.offline && (
            <div
              className={`flex flex-wrap items-center gap-2.5 rounded-xl border px-3 py-2.5 text-sm ${
                goodRun
                  ? "border-emerald-500/25 bg-emerald-500/[0.07] text-emerald-200"
                  : "border-amber-500/25 bg-amber-500/[0.07] text-amber-100"
              }`}
            >
              <CircleCheck size={16} className="shrink-0" />
              <span>
                Ran <b className="font-semibold">{result.name}</b>
              </span>
              {result.status && <Badge value={result.status} />}
              <span className="opacity-70">
                · {result.sessions} session{result.sessions === 1 ? "" : "s"} spawned
              </span>
            </div>
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
