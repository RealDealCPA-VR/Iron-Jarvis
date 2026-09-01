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
  CopyPlus,
  MessageCircleQuestion,
  TriangleAlert,
  X,
} from "lucide-react";
import { get, post, patch, del, ApiError } from "@/lib/api";
import {
  WORKFLOW_RUN_TERMINAL,
  type WorkflowRun,
  type WorkflowStep,
} from "@/lib/types";
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
import { TriggerInspector } from "./TriggerInspector";
import {
  announceWorkflowsChanged,
  WORKFLOWS_LIST_EVENT,
  type WorkflowsListDetail,
} from "./SavedWorkflows";
import {
  agentMeta,
  type StepExpect,
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

/** The ONE canvas step shape (v1.170.0): the shared WorkflowStep from
 *  lib/types plus the optional `expect` checks (engine v1.170.0 — not yet on
 *  the shared type). Replaces the local RawStep duplicate. */
export type CanvasStep = WorkflowStep & {
  expect?: StepExpect | null;
};

/** Turn a saved `[{name,agent,task}]` list into a Trigger → step₁ → … chain
 *  laid out left-to-right, mirroring the seed graph's geometry. */
export function buildGraph(steps: CanvasStep[]): { nodes: Node[]; edges: Edge[] } {
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
    const node = mkStep(
      id,
      s.name?.trim() || `Step ${i + 1}`,
      agent,
      s.task ?? "",
      STEP_X0 + i * STEP_DX,
      STEP_Y,
      s.tool ?? null,
    );
    // v1.121.0 fields ride node data verbatim — the editor must never
    // silently strip a kind/gate/group off a def it re-saves.
    node.data = {
      ...node.data,
      kind: s.kind ?? "agent",
      on_failure: s.on_failure ?? "halt",
      group: s.group ?? null,
      args: s.args && typeof s.args === "object" ? s.args : {},
      message: s.message ?? "",
      // v1.170.0: `expect` rides through load → edit → save untouched (null
      // when absent so the serializer knows to omit the key entirely).
      expect: s.expect && typeof s.expect === "object" ? s.expect : null,
    };
    nodes.push(node);
    edges.push(mkEdge(prev, id));
    prev = id;
  });
  return { nodes, edges };
}

/** Parse a `steps_json` string into a CanvasStep[] (tolerant of bad data). */
export function parseSteps(stepsJson: string | undefined | null): CanvasStep[] {
  try {
    const parsed = JSON.parse(stepsJson || "[]");
    return Array.isArray(parsed) ? (parsed as CanvasStep[]) : [];
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

/* ---- ONE serializer (v1.170.0) ------------------------------------------- */

/** The single graph → steps serializer, used by BOTH save() and run() (they
 *  used to hold divergent inline copies). Emits exactly the pre-v1.170.0 nine
 *  fields; `expect` is added ONLY when it carries a non-empty check AND the
 *  step is an agent/tool kind — the same gate the inspector's "Prove it"
 *  section and the engine itself apply. Without the kind gate, an agent step
 *  given a `files` expect and then switched to Notify kept an INVISIBLE check
 *  (the inspector only renders expect for agent/tool) that the engine fails
 *  deterministically on every run ("'files' expectations need an agent or
 *  tool step"), with no UI path to remove it. Defs that never used expect
 *  serialize byte-identically to before. */
export function stepsFromGraph(nodes: Node[], edges: Edge[]): CanvasStep[] {
  return orderedSteps(nodes, edges).map((n, i) => {
    const d = n.data as StepNodeData;
    const kind = d.kind ?? "agent";
    const step: CanvasStep = {
      name: d.name?.trim() || `step-${i + 1}`,
      agent: d.agent,
      task: (d.task ?? "").trim(),
      tool: d.tool ?? null,
      kind,
      on_failure: d.on_failure ?? "halt",
      group: d.group ?? null,
      args: d.args ?? {},
      message: d.message ?? "",
    };
    if (kind === "agent" || kind === "tool") {
      const files = (d.expect?.files ?? []).filter((f) => f && f.trim());
      const contains = (d.expect?.summary_contains ?? []).filter(
        (s) => s && s.trim(),
      );
      if (files.length || contains.length) {
        step.expect = {
          ...(files.length ? { files } : {}),
          ...(contains.length ? { summary_contains: contains } : {}),
        };
      }
    }
    return step;
  });
}

/* ---- DAG honesty (v1.170.0) ---------------------------------------------- */

/** Why a new edge must be refused, or null when it may be drawn. The engine
 *  runs ONE linear chain (parallelism = adjacent steps sharing a group), so a
 *  second outgoing edge draws a branch that will never run as drawn. Re-drawing
 *  an existing edge is a no-op (addEdge dedupes) and is not refused. */
export function connectionRefusal(edges: Edge[], c: Connection): string | null {
  if (!c.source || !c.target) return null;
  if (c.source === c.target) return "A step can't feed itself.";
  const other = edges.some(
    (e) => e.source === c.source && e.target !== c.target,
  );
  if (other) {
    return (
      "One outgoing link per step — the engine runs a single chain. " +
      "Branches run via Parallel group instead: set the same group on adjacent steps."
    );
  }
  return null;
}

/** Node ids whose parallel group is SPLIT in serialized order (members not
 *  adjacent): the engine batches only consecutive same-group steps, so a split
 *  group silently degrades to separate batches — surface it on the cards. */
export function splitGroupNodeIds(nodes: Node[], edges: Edge[]): Set<string> {
  const steps = orderedSteps(nodes, edges);
  const positions = new Map<string, { ids: string[]; indices: number[] }>();
  steps.forEach((n, i) => {
    const g = ((n.data as StepNodeData).group ?? "").trim();
    if (!g) return;
    const entry = positions.get(g) ?? { ids: [], indices: [] };
    entry.ids.push(n.id);
    entry.indices.push(i);
    positions.set(g, entry);
  });
  const out = new Set<string>();
  for (const { ids, indices } of positions.values()) {
    if (ids.length < 2) continue;
    const span = Math.max(...indices) - Math.min(...indices) + 1;
    if (span !== ids.length) for (const id of ids) out.add(id);
  }
  return out;
}

/* ---- Rename (PATCH, contract 3) + run body ------------------------------- */

/** Rename a SAVED def in place via PATCH /workflows/{name} — moves the pin
 *  row server-side and migrates the locally-saved layout — instead of forking
 *  a second row. Returns false when the old row no longer exists (404): the
 *  caller's plain save is then the correct outcome. 409 (name taken) and every
 *  other failure propagate for honest surfacing. */
export async function renameSavedDef(
  oldName: string,
  newName: string,
): Promise<boolean> {
  try {
    await patch(`/workflows/${encodeURIComponent(oldName)}`, {
      new_name: newName,
    });
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return false;
    throw err;
  }
  try {
    const raw = localStorage.getItem(layoutKey(oldName));
    if (raw != null) {
      localStorage.setItem(layoutKey(newName), raw);
      localStorage.removeItem(layoutKey(oldName));
    }
  } catch {
    /* ignore (private mode / quota) */
  }
  return true;
}

/** Build the POST /workflows/run body. The loaded def's project pin is
 *  PRESERVED explicitly (contract 1: an explicit project_id wins) — without it
 *  a rename-then-run loses the pin because the server can only inherit by
 *  name. The key is OMITTED when there is no pin: sending "" would force an
 *  unpinned run, which is not the same thing. */
export function buildRunBody(
  name: string,
  steps: CanvasStep[],
  loadedPin: string | null,
): { name: string; steps: CanvasStep[]; project_id?: string } {
  const body: { name: string; steps: CanvasStep[]; project_id?: string } = {
    name,
    steps,
  };
  if (loadedPin) body.project_id = loadedPin;
  return body;
}

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

// v1.170.0: the shared WORKFLOW_RUN_TERMINAL (lib/types) replaced the local
// copy — `waiting` (parked on an ask) and `resuming` are NOT terminal; the
// poll continues through both.

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

/** The parked ask (v1.170.0): what a `waiting` run is waiting FOR. Parsed from
 *  `waiting_json` ({index, step, question, options?} — workflows/engine.py);
 *  null unless the run's status is exactly "waiting". Corrupt JSON still
 *  returns an honest generic ask — the run IS parked either way. */
export interface WaitingAsk {
  index: number;
  step: string;
  question: string;
  options: string[];
}

export function parseWaiting(run: WorkflowRun): WaitingAsk | null {
  if (String(run.status ?? "") !== "waiting") return null;
  const fallback: WaitingAsk = {
    index: -1,
    step: "",
    question: "This run needs your answer.",
    options: [],
  };
  try {
    const w = JSON.parse(String((run as { waiting_json?: string }).waiting_json || "{}"));
    if (!w || typeof w !== "object" || Array.isArray(w)) return fallback;
    return {
      index: Number.isInteger(w.index) ? (w.index as number) : -1,
      step: typeof w.step === "string" ? w.step : "",
      question:
        typeof w.question === "string" && w.question.trim()
          ? w.question
          : fallback.question,
      options: Array.isArray(w.options)
        ? (w.options as unknown[]).filter((o): o is string => typeof o === "string")
        : [],
    };
  } catch {
    return fallback;
  }
}

/** Merge the ordered steps_json with the live outputs_json into one view list.
 *  While the run is active, the first step lacking an output entry is the one
 *  currently "running"; later un-entered steps are "pending". A `waiting` run
 *  is NOT running — the parked ask step is "waiting" (on you), never "running"
 *  (v1.170.0: labelling a parked run "running" was a lie about who's working). */
export function runStepViews(run: WorkflowRun): RunStepView[] {
  const steps = parseSteps((run as { steps_json?: string }).steps_json);
  const outputs = parseOutputs((run as { outputs_json?: string }).outputs_json);
  const waiting = parseWaiting(run);
  const active = !WORKFLOW_RUN_TERMINAL.has(String(run.status ?? ""));
  let liveAssigned = false;
  return steps.map((s, i) => {
    const nm = s.name?.trim() || `step-${i + 1}`;
    const out = outputs[nm];
    let status: string;
    if (out?.status) status = out.status;
    else if (waiting) {
      // Exactly ONE parked step: matched by name, else by index, else the
      // first output-less step (corrupt waiting_json). The rest are pending.
      const parked = waiting.step
        ? nm === waiting.step
        : waiting.index >= 0
          ? i === waiting.index
          : !liveAssigned;
      if (parked && !liveAssigned) {
        status = "waiting";
        liveAssigned = true;
      } else status = "pending";
    } else if (active && !liveAssigned) {
      status = "running";
      liveAssigned = true;
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
  waiting: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  failed: "border-rose-500/30 bg-rose-500/10 text-rose-300",
  skipped: "border-white/[0.08] bg-white/[0.02] text-zinc-500",
  pending: "border-white/[0.08] bg-white/[0.02] text-zinc-500",
};

function ChipIcon({ status }: { status: string }) {
  if (status === "completed") return <CircleCheck size={12} />;
  if (status === "failed") return <CircleX size={12} />;
  if (status === "running") return <Loader2 size={12} className="animate-spin" />;
  if (status === "waiting") return <MessageCircleQuestion size={12} />;
  if (status === "skipped") return <MinusCircle size={12} />;
  return <Circle size={12} />;
}

/** The live run strip: a status header + cancel, a chip per step, an inline
 *  answer box while the run is parked on an `ask` step (v1.170.0), and an
 *  honest collapsible per-step results panel (summaries; failures in red). */
export function RunProgress({
  run,
  onCancel,
  cancelling,
  onAnswered,
}: {
  run: WorkflowRun;
  onCancel: () => void;
  cancelling: boolean;
  /** Called with the answer route's returned status so the owner can reflect
   *  the resume immediately instead of waiting a poll tick. */
  onAnswered?: (status: string) => void;
}) {
  const steps = runStepViews(run);
  const status = String(run.status ?? "running");
  const active = !WORKFLOW_RUN_TERMINAL.has(status);
  const waiting = parseWaiting(run);
  const [open, setOpen] = useState<string | null>(null);
  const hasResults = steps.some((s) => s.summary || s.status === "failed");

  /* Inline answer box state. Reset when a DIFFERENT ask parks the run (a
     later ask step must not inherit the previous conflict/error). */
  const [answerText, setAnswerText] = useState("");
  const [answering, setAnswering] = useState(false);
  const [answerError, setAnswerError] = useState<string | null>(null);
  const [conflicted, setConflicted] = useState(false);
  const askKey = waiting ? `${waiting.index}:${waiting.step}:${waiting.question}` : "";
  useEffect(() => {
    setAnswerText("");
    setAnswering(false);
    setAnswerError(null);
    setConflicted(false);
  }, [askKey]);

  const submitAnswer = async (text: string) => {
    const answer = text.trim();
    const id = run.id ? String(run.id) : "";
    if (!answer || !id || answering) return;
    setAnswering(true);
    setAnswerError(null);
    try {
      const res = await post<{ id: string; status?: string }>(
        `/workflows/runs/${encodeURIComponent(id)}/answer`,
        { answer },
      );
      setAnswerText("");
      onAnswered?.(String(res.status ?? "running"));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Answered from another surface (chat card / bell) — the atomic
        // waiting→resuming claim lost. Honest note, no retry; the poll
        // lands the true state.
        setConflicted(true);
        setAnswerError(err.message);
      } else {
        setAnswerError(err instanceof ApiError ? err.message : String(err));
      }
    } finally {
      setAnswering(false);
    }
  };

  return (
    <div className="space-y-2.5 rounded-xl border border-white/[0.08] bg-ink-950/40 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {/* waiting/resuming get a REAL badge (amber/cyan), not the raw slate
            fallback the generic status map would render. */}
        {status === "waiting" ? (
          <Badge value="waiting on you" tone="amber" />
        ) : status === "resuming" ? (
          <Badge value="resuming" tone="cyan" />
        ) : (
          <Badge value={status} />
        )}
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
            title={`${s.name} — ${s.status === "waiting" ? "waiting on you" : s.status}`}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium ${
              CHIP_TONE[s.status] ?? CHIP_TONE.pending
            }`}
          >
            <ChipIcon status={s.status} />
            <span className="max-w-[140px] truncate">{s.name}</span>
            {s.status === "waiting" && (
              <span className="shrink-0 opacity-80">· waiting on you</span>
            )}
          </span>
        ))}
      </div>

      {/* Ask gate (v1.170.0): the run is parked on an ask step — the question
          and an inline answer box, right where the user is watching the run.
          POSTs the existing /workflows/runs/{id}/answer; a 409 (answered from
          the chat card or the bell first) surfaces honestly, never retries. */}
      {waiting && !conflicted && (
        <div
          data-testid="run-ask-gate"
          className="space-y-2 rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2.5"
        >
          <p className="flex items-start gap-2 text-[12px] leading-relaxed text-amber-200">
            <MessageCircleQuestion size={14} className="mt-0.5 shrink-0" />
            <span className="whitespace-pre-wrap">{waiting.question}</span>
          </p>
          {waiting.options.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {waiting.options.map((o) => (
                <button
                  key={o}
                  type="button"
                  disabled={answering}
                  onClick={() => submitAnswer(o)}
                  className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-200 transition-colors hover:border-amber-500/60 hover:bg-amber-500/20 disabled:opacity-50"
                >
                  {o}
                </button>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2">
            <input
              value={answerText}
              onChange={(e) => setAnswerText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitAnswer(answerText);
              }}
              placeholder="Type your answer — the run continues from here"
              aria-label={`Answer workflow ${String(run.workflow_name ?? "")}`}
              className="field min-w-0 flex-1 text-[12px]"
            />
            <button
              type="button"
              onClick={() => submitAnswer(answerText)}
              disabled={answering || !answerText.trim()}
              className="shrink-0 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs font-semibold text-amber-200 transition-colors hover:border-amber-500/60 hover:bg-amber-500/20 disabled:opacity-50"
            >
              {answering ? "Sending…" : "Answer"}
            </button>
          </div>
          {answerError && (
            <p className="text-[11.5px] text-rose-300">{answerError}</p>
          )}
        </div>
      )}
      {conflicted && answerError && (
        <p data-testid="run-ask-conflict" className="text-[11.5px] text-amber-200/90">
          Already answered elsewhere — {answerError}
        </p>
      )}

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

  /* v1.170.0 — which SAVED def is on the canvas (rename + pin preservation).
     loadedName: the def's name at load time (rename PATCHes from it);
     loadedPin: its project pin, carried into every run of the edited graph. */
  const [loadedName, setLoadedName] = useState<string | null>(null);
  const [loadedPin, setLoadedPin] = useState<string | null>(null);
  const pinFetchRef = useRef<string | null>(null);

  /* v1.170.0 — DAG-honesty notice for a refused edge (inline, auto-clears). */
  const [edgeNotice, setEdgeNotice] = useState<string | null>(null);
  const edgeNoticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showEdgeNotice = useCallback((msg: string) => {
    setEdgeNotice(msg);
    if (edgeNoticeTimer.current) clearTimeout(edgeNoticeTimer.current);
    edgeNoticeTimer.current = setTimeout(() => setEdgeNotice(null), 8000);
  }, []);
  useEffect(
    () => () => {
      if (edgeNoticeTimer.current) clearTimeout(edgeNoticeTimer.current);
    },
    [],
  );

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

  /* Surface non-adjacent same-group degradation (v1.170.0): flag every node
     whose parallel group is split by other steps in serialized order — the
     engine batches only CONSECUTIVE same-group steps, so a split group runs
     as separate batches. Keyed on the group signature (not node data writes),
     so setting groupSplit below cannot re-trigger it. */
  const groupSig = nodes
    .filter((n) => n.type === "step")
    .map((n) => `${n.id}:${((n.data as StepNodeData).group ?? "").trim()}`)
    .join("|");
  useEffect(() => {
    setNodes((nds) => {
      const split = splitGroupNodeIds(nds, edges);
      let changed = false;
      const next = nds.map((n) => {
        if (n.type !== "step") return n;
        const flag = split.has(n.id);
        if (Boolean((n.data as StepNodeData).groupSplit) === flag) return n;
        changed = true;
        return { ...n, data: { ...n.data, groupSplit: flag } };
      });
      return changed ? next : nds;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupSig, edges, setNodes]);

  const onConnect = useCallback(
    (c: Connection) => {
      // DAG honesty (v1.170.0): the engine runs ONE chain — refuse drawing a
      // branch it would never run, and say how branching actually works.
      const refusal = connectionRefusal(edges, c);
      if (refusal) {
        showEdgeNotice(refusal);
        return;
      }
      setEdges((eds) => addEdge({ ...c, animated: true }, eds));
    },
    [edges, setEdges, showEdgeNotice],
  );

  const onNodeClick = useCallback(
    // The trigger is selectable too (v1.122.0) — it opens the "when should
    // this run?" panel instead of the step inspector.
    (_: unknown, node: Node) =>
      setSelectedId(node.type === "step" || node.id === "trigger" ? node.id : null),
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

  // v1.222.0: the page's Saved-workflows list can delete a row this canvas
  // is editing. Refresh the Load ▾ list, and if the deleted def is the one
  // loaded here, stop treating it as saved — a later Save must be a fresh
  // create (never a rename PATCH against a deleted row) and a Run must not
  // carry the deleted def's pin. Same clearing the dropdown's own delete does.
  useEffect(() => {
    const onListChanged = (e: Event) => {
      const detail = (e as CustomEvent).detail as WorkflowsListDetail | undefined;
      refreshDefs();
      const gone = detail?.deleted;
      if (!gone) return;
      try {
        localStorage.removeItem(layoutKey(gone));
      } catch {
        /* ignore */
      }
      if (gone === loadedName) {
        setLoadedName(null);
        setLoadedPin(null);
        pinFetchRef.current = null;
      }
    };
    window.addEventListener(WORKFLOWS_LIST_EVENT, onListChanged);
    return () => window.removeEventListener(WORKFLOWS_LIST_EVENT, onListChanged);
  }, [refreshDefs, loadedName]);

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
      // Track the loaded def for rename-in-place + pin-preserving runs. The
      // list endpoint omits project_id, so fetch the pin from the detail
      // route (stale-response guard: only the LATEST load may land it).
      setLoadedName(def.name);
      setLoadedPin(def.project_id ?? null);
      pinFetchRef.current = def.name;
      if (def.project_id === undefined) {
        get<{ project_id?: string | null }>(
          `/workflows/${encodeURIComponent(def.name)}`,
        )
          .then((r) => {
            if (pinFetchRef.current === def.name)
              setLoadedPin(r.project_id ?? null);
          })
          .catch(() => {
            /* offline/404: run() then omits project_id and the server
               inherits the pin by name — the pre-v1.170.0 behavior. */
          });
      }
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

  const save = useCallback(
    async (opts?: { asNew?: boolean }) => {
      setError(null);
      setSuccess(null);
      setResult(null);
      const steps = stepsFromGraph(nodes, edges);
      const wfName = name.trim();
      if (!wfName) {
        setError("Name the workflow before saving.");
        return;
      }
      if (steps.length === 0) {
        setError("Add at least one step before saving.");
        return;
      }
      // Rename-in-place (v1.170.0, contract 3): editing the name of a LOADED
      // def and saving used to fork a second row (the old name lived on with
      // stale steps and kept the pin). Default = PATCH rename first, then
      // save the steps under the new name; "Save as new" keeps the fork
      // behavior, explicitly.
      const renaming = !opts?.asNew && !!loadedName && wfName !== loadedName;
      let renamed = false;
      setSaving(true);
      try {
        if (renaming && loadedName) {
          // false = the old row vanished (deleted elsewhere) — the plain
          // save below is then the correct outcome, not an error.
          renamed = await renameSavedDef(loadedName, wfName);
        }
        await post("/workflows", {
          name: wfName,
          steps,
          description: "saved from the workflow editor",
        });
        // Persist the current node layout so a later Load restores it verbatim.
        saveLayout(wfName, nodes);
        setLoadedName(wfName);
        if (opts?.asNew && wfName !== loadedName) {
          // "Save as new" forks an UNPINNED row: POST /workflows preserves
          // the pin of the name it saves, and a fresh name has none. Keep the
          // local pin in sync — carrying the PARENT's pin forward would make
          // run() ground the fork's canvas runs in the parent's project while
          // every other surface runs the same saved def unpinned. Also point
          // the stale-response guard at the fork so an in-flight pin fetch
          // for the parent can no longer land its pin here.
          setLoadedPin(null);
          pinFetchRef.current = wfName;
        }
        setSuccess(
          renamed
            ? `Renamed “${loadedName}” to “${wfName}” and saved ${steps.length} step${steps.length === 1 ? "" : "s"}.`
            : `Saved “${wfName}” — ${steps.length} step${steps.length === 1 ? "" : "s"}. It’s in the Load list.`,
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
        // The page's Saved-workflows list shows the new/renamed row at once.
        announceWorkflowsChanged({ saved: wfName });
        await refreshDefs();
      } catch (err) {
        if (err instanceof ApiError && err.status === 0)
          setResult({ offline: true });
        else setError(err instanceof ApiError ? err.message : String(err));
      } finally {
        setSaving(false);
      }
    },
    [nodes, edges, name, loadedName, refreshDefs],
  );

  const run = useCallback(async () => {
    setError(null);
    setResult(null);
    setSuccess(null);
    setActiveRun(null);
    setCancelling(false);
    stopPolling();
    const steps = stepsFromGraph(nodes, edges);
    const wfName = name.trim() || "demo-workflow";
    if (steps.length === 0) {
      setError("Add at least one step before running.");
      return;
    }
    setBusy(true);
    try {
      // POST returns the freshly-created record AT ONCE (status "running"); the
      // engine runs the steps in the background. Poll for progress every 2s.
      // The loaded def's project pin rides along explicitly (buildRunBody) so
      // an edited/renamed graph keeps running grounded in its project.
      const rec = await post<WorkflowRun>(
        "/workflows/run",
        buildRunBody(wfName, steps, loadedPin),
      );
      setActiveRun(rec);
      const runId = rec.id ? String(rec.id) : "";
      if (!runId || WORKFLOW_RUN_TERMINAL.has(String(rec.status ?? ""))) {
        setBusy(false);
        return;
      }
      // The poll continues through `waiting` (parked on an ask) and
      // `resuming` — WORKFLOW_RUN_TERMINAL excludes both by design.
      pollRef.current = setInterval(async () => {
        try {
          const fresh = await get<WorkflowRun>(
            `/workflows/runs/${encodeURIComponent(runId)}`,
          );
          setActiveRun(fresh);
          if (WORKFLOW_RUN_TERMINAL.has(String(fresh.status ?? ""))) {
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
  }, [nodes, edges, name, loadedPin, stopPolling]);

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
      // If the canvas is editing the row being deleted, its loaded-def
      // tracking is now stale: a later Save must be a fresh create (never a
      // rename PATCH against a deleted row) and a Run must not carry the
      // deleted def's pin. Cleared on 404 too — the row is equally gone.
      const clearIfLoaded = () => {
        if (defName !== loadedName) return;
        setLoadedName(null);
        setLoadedPin(null);
        pinFetchRef.current = null;
      };
      try {
        await del(`/workflows/${encodeURIComponent(defName)}`);
        try {
          localStorage.removeItem(layoutKey(defName));
        } catch {
          /* ignore */
        }
        clearIfLoaded();
        setSuccess(`Deleted “${defName}”.`);
        announceWorkflowsChanged({ deleted: defName });
        await refreshDefs();
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          clearIfLoaded();
          announceWorkflowsChanged({ deleted: defName });
          await refreshDefs();
        } else setError(err instanceof ApiError ? err.message : String(err));
      }
    },
    [refreshDefs, loadedName],
  );

  const selected = nodes.find((n) => n.id === selectedId && n.type === "step");
  const selData = selected?.data as StepNodeData | undefined;
  const stepCount = nodes.filter((n) => n.type === "step").length;
  // The name box differs from the loaded def → Save becomes a rename-in-place
  // (PATCH), with fork-a-copy still available explicitly.
  const renamePending = !!loadedName && !!name.trim() && name.trim() !== loadedName;

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
                          // Always visible (v1.222.0): at opacity 0 until hover
                          // nobody found it — the user reported having no way
                          // to delete a workflow while this button existed.
                          className="shrink-0 rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-rose-500/10 hover:text-rose-300"
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
            onClick={() => save()}
            disabled={saving}
            title={
              renamePending
                ? `Rename “${loadedName}” to “${name.trim()}” and save — its pin and schedule follow the new name`
                : undefined
            }
            className="btn-ghost"
          >
            {saving ? (
              <LoaderInline label="Saving…" />
            ) : renamePending ? (
              <>
                <Save size={15} /> Rename &amp; save
              </>
            ) : (
              <>
                <Save size={15} /> Save
              </>
            )}
          </button>
          {renamePending && (
            <button
              type="button"
              onClick={() => save({ asNew: true })}
              disabled={saving}
              title={`Keep “${loadedName}” and save a separate copy named “${name.trim()}”`}
              className="btn-ghost"
            >
              <CopyPlus size={15} /> Save as new
            </button>
          )}
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

        {/* Inline DAG-honesty notice: why the edge was refused, and what to do
            instead. Auto-clears; dismissable. */}
        {edgeNotice && (
          <div
            data-testid="edge-notice"
            className="absolute left-1/2 top-3 z-30 flex max-w-[420px] -translate-x-1/2 items-start gap-2 rounded-xl border border-amber-500/30 bg-ink-950/90 px-3 py-2 text-[12px] leading-relaxed text-amber-200 shadow-card"
          >
            <TriangleAlert size={14} className="mt-0.5 shrink-0" />
            <span>{edgeNotice}</span>
            <button
              type="button"
              onClick={() => setEdgeNotice(null)}
              aria-label="Dismiss"
              className="ml-1 shrink-0 rounded-md p-0.5 text-amber-200/70 transition-colors hover:text-amber-100"
            >
              <X size={13} />
            </button>
          </div>
        )}

        {selData && (
          <NodeInspector
            data={selData}
            onChange={(patch) => updateData(selected!.id, patch)}
            onDelete={() => deleteNode(selected!.id)}
            onClose={() => setSelectedId(null)}
          />
        )}
        {selectedId === "trigger" && (
          <TriggerInspector
            workflowName={name}
            saved={defs.some((d) => d.name === name.trim())}
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
              onAnswered={(status) =>
                // Reflect the resume at once (the 2s poll lands the rest);
                // waiting_json is cleared so the ask box leaves immediately.
                setActiveRun((r) =>
                  r ? { ...r, status, waiting_json: "" } : r,
                )
              }
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
