"use client";

// Graph view of memory: every remembered item (lesson / working-memory row /
// long-term note) is a node; dashed edges are daemon-computed similarity,
// solid cyan edges are links the user drew. Mirrors the WorkflowCanvas
// @xyflow/react conventions (dark colorMode, dotted cyan background,
// module-scope nodeTypes/defaultEdgeOptions, provider-wrapped default export).
//
// Backend contract (fixed):
//   GET  /memory/graph?threshold=0.45 -> { nodes, edges, embedder, note? }
//   POST /memory/graph/link {a,b}     -> { linked, note? }
//   POST /memory/graph/unlink {a,b}   -> { removed: "manual"|"auto", blocked }
// Unlinking a similarity edge BLOCKS it server-side — it stays gone.

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type Connection,
  type DefaultEdgeOptions,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {
  BrainCircuit,
  Database,
  GraduationCap,
  Link2,
  RefreshCw,
  Unlink,
  Waypoints,
  X,
  type LucideIcon,
} from "lucide-react";
import { get, post, ApiError } from "@/lib/api";
import { Empty, ErrorNote, OfflineHint, SkeletonRows } from "@/components/ui";

/* ---- Backend DTOs --------------------------------------------------------- */

type MemGroup = "lesson" | "memory" | "note";
type EdgeKind = "similar" | "manual";

interface GraphNodeDto {
  id: string;
  label: string;
  group: MemGroup;
  snippet: string;
  meta?: Record<string, unknown>;
}
interface GraphEdgeDto {
  a: string;
  b: string;
  weight: number;
  kind: EdgeKind;
}
interface GraphDto {
  nodes: GraphNodeDto[];
  edges: GraphEdgeDto[];
  embedder: string;
  note?: string;
}

interface MemNodeData {
  label: string;
  group: MemGroup;
  snippet: string;
  meta?: Record<string, unknown>;
  [key: string]: unknown;
}
interface MemEdgeData {
  a: string;
  b: string;
  kind: EdgeKind;
  weight: number;
  [key: string]: unknown;
}

/* ---- Group styling (matches the badge tones the memory scopes use:
       lessons = amber, working memory = cyan/accent, long-term = emerald) --- */

const GROUP_META: Record<
  MemGroup,
  { label: string; hex: string; icon: LucideIcon; tile: string; chip: string; dot: string }
> = {
  lesson: {
    label: "lesson",
    hex: "#fbbf24",
    icon: GraduationCap,
    tile: "border-amber-500/30 bg-amber-500/10 text-amber-300",
    chip: "border-amber-500/25 bg-amber-500/10 text-amber-300",
    dot: "bg-amber-400",
  },
  memory: {
    label: "memory",
    hex: "#22d3ee",
    icon: BrainCircuit,
    tile: "border-accent/30 bg-accent/10 text-accent-soft",
    chip: "border-accent/30 bg-accent/10 text-accent-soft",
    dot: "bg-accent",
  },
  note: {
    label: "note",
    hex: "#34d399",
    icon: Database,
    tile: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    chip: "border-emerald-500/25 bg-emerald-500/10 text-emerald-300",
    dot: "bg-emerald-400",
  },
};

function normGroup(g: unknown): MemGroup {
  return g === "lesson" || g === "note" ? g : "memory";
}
const groupMeta = (g: unknown) => GROUP_META[normGroup(g)];

/* ---- Custom node ----------------------------------------------------------
   Every node is BOTH a connection source and target: subtle left target +
   right source handles let the user drag a manual link between any two. */

const handleClass =
  "!h-2.5 !w-2.5 !rounded-full !border !border-ink-950 !bg-accent/80 " +
  "opacity-50 transition-opacity hover:opacity-100";

function MemoryNodeImpl({ data, selected, isConnectable }: NodeProps) {
  const d = data as MemNodeData;
  const meta = groupMeta(d.group);
  const Icon = meta.icon;
  return (
    <div
      className={`w-[190px] rounded-xl border bg-ink-850/90 px-3 py-2 backdrop-blur-sm transition-all duration-150 ${
        selected
          ? "border-accent/70 shadow-glow"
          : "border-white/[0.08] shadow-card hover:border-accent/40"
      }`}
    >
      <Handle
        type="target"
        position={Position.Left}
        isConnectable={isConnectable}
        className={handleClass}
      />
      <div className="flex items-center gap-2">
        <span
          className={`grid h-6 w-6 shrink-0 place-items-center rounded-md border ${meta.tile}`}
          title={meta.label}
        >
          <Icon size={13} />
        </span>
        <span
          className="min-w-0 flex-1 truncate text-[12px] font-medium text-zinc-100"
          title={d.label}
        >
          {d.label}
        </span>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        isConnectable={isConnectable}
        className={handleClass}
      />
    </div>
  );
}
const MemoryNode = memo(MemoryNodeImpl);

/* nodeTypes / edge defaults must be stable references (module scope). */
const nodeTypes = { memory: MemoryNode };

/* Manual-link look — also what a freshly dragged connection line inherits.
   No arrowheads anywhere: memory links are undirected. */
const defaultEdgeOptions: DefaultEdgeOptions = {
  animated: true,
  style: { stroke: "#22d3ee", strokeWidth: 2.25 },
};

/* ---- Edges ---------------------------------------------------------------- */

/** Canonical undirected pair id, so (a,b) and (b,a) collapse to one edge. */
const pairId = (a: string, b: string) => (a < b ? `${a}__${b}` : `${b}__${a}`);
const edgeId = (a: string, b: string) => `mem:${pairId(a, b)}`;

function buildEdge(e: GraphEdgeDto): Edge {
  const weight = Number.isFinite(e.weight) ? Math.min(1, Math.max(0, e.weight)) : 0.5;
  const manual = e.kind === "manual";
  const data: MemEdgeData = { a: e.a, b: e.b, kind: manual ? "manual" : "similar", weight };
  return {
    id: edgeId(e.a, e.b),
    source: e.a,
    target: e.b,
    animated: manual,
    data,
    style: manual
      ? { stroke: "#22d3ee", strokeWidth: 2.25 }
      : {
          stroke: "#67e8f9",
          strokeWidth: 1.5,
          strokeDasharray: "6 4",
          // Similarity strength drives visibility: weak = faint, strong = clear.
          opacity: 0.25 + weight * 0.5,
        },
  };
}

/* ---- Deterministic layout (no Math.random) --------------------------------
   The three groups cluster at fixed angles around the center; within a
   cluster, members sit on a golden-angle (Fermat) spiral so the radius grows
   with index and nothing stacks. Same input -> same picture, every time. */

const GOLDEN_ANGLE = 2.399963229728653; // radians
const GROUP_ANGLE: Record<MemGroup, number> = {
  lesson: -Math.PI / 2, // top
  memory: Math.PI / 6, // bottom-right
  note: (5 * Math.PI) / 6, // bottom-left
};
const SPIRAL_STEP = 120; // ~min neighbor distance within a cluster

function layoutNodes(
  dtoNodes: GraphNodeDto[],
  keepPositions: Map<string, { x: number; y: number }>,
): Node[] {
  const groups: Record<MemGroup, GraphNodeDto[]> = { lesson: [], memory: [], note: [] };
  for (const n of dtoNodes) groups[normGroup(n.group)].push(n);
  const maxGroup = Math.max(groups.lesson.length, groups.memory.length, groups.note.length, 1);
  // Push clusters apart far enough that their spirals can't collide.
  const clusterR = dtoNodes.length <= 1 ? 0 : 180 + SPIRAL_STEP * 1.15 * Math.sqrt(maxGroup);

  const out: Node[] = [];
  (Object.keys(groups) as MemGroup[]).forEach((g) => {
    const angle = GROUP_ANGLE[g];
    const cx = Math.cos(angle) * clusterR;
    const cy = Math.sin(angle) * clusterR;
    groups[g].forEach((n, i) => {
      const r = SPIRAL_STEP * Math.sqrt(i);
      const th = angle + i * GOLDEN_ANGLE;
      out.push({
        id: n.id,
        type: "memory",
        // Nodes are memories, not canvas artifacts — Delete only removes edges.
        deletable: false,
        // A user-dragged position survives Refresh; new nodes get the spiral.
        position: keepPositions.get(n.id) ?? {
          x: cx + r * Math.cos(th),
          y: cy + r * Math.sin(th),
        },
        data: {
          label: n.label,
          group: normGroup(n.group),
          snippet: n.snippet,
          meta: n.meta,
        } satisfies MemNodeData,
      });
    });
  });
  return out;
}

/* ---- Node details side panel (NodeInspector-style overlay) ---------------- */

function NodePanel({
  nodeId,
  data,
  nodeEdges,
  labelOf,
  linking,
  onToggleLink,
  onDisconnect,
  onClose,
}: {
  nodeId: string;
  data: MemNodeData;
  nodeEdges: Edge[];
  labelOf: (id: string) => string;
  linking: boolean;
  onToggleLink: () => void;
  onDisconnect: (edge: Edge) => void;
  onClose: () => void;
}) {
  const meta = groupMeta(data.group);
  const Icon = meta.icon;
  const metaEntries = data.meta ? Object.entries(data.meta) : [];
  return (
    <div className="card-surface absolute bottom-3 right-3 top-3 z-20 flex w-[320px] flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-3 border-b hairline px-4 py-3">
        <h3 className="flex min-w-0 items-center gap-2 text-[13px] font-semibold text-zinc-200">
          <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-md border ${meta.tile}`}>
            <Icon size={13} />
          </span>
          <span className="truncate" title={data.label}>
            {data.label}
          </span>
        </h3>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close details"
          className="rounded-lg border border-white/10 p-1 text-zinc-500 transition-colors hover:border-white/20 hover:text-zinc-200"
        >
          <X size={14} />
        </button>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <span
          className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${meta.chip}`}
        >
          {meta.label}
        </span>

        <div>
          <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
            Remembered
          </label>
          <p className="whitespace-pre-wrap rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 text-[12.5px] leading-relaxed text-zinc-300">
            {data.snippet || <span className="italic text-zinc-600">No snippet.</span>}
          </p>
        </div>

        {metaEntries.length > 0 && (
          <div>
            <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
              Meta
            </label>
            <dl className="space-y-1.5">
              {metaEntries.map(([k, v]) => {
                const text = typeof v === "object" ? JSON.stringify(v) : String(v);
                return (
                  <div key={k} className="flex items-start justify-between gap-3 text-[12px]">
                    <dt className="shrink-0 text-zinc-500">{k}</dt>
                    <dd
                      className="min-w-0 truncate text-right font-mono text-[11.5px] text-zinc-300"
                      title={text}
                    >
                      {text}
                    </dd>
                  </div>
                );
              })}
            </dl>
          </div>
        )}

        <div>
          <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
            Connections ({nodeEdges.length})
          </label>
          {nodeEdges.length === 0 ? (
            <p className="text-[12px] text-zinc-500">
              Nothing linked yet — drag from this node, or use Connect to…
            </p>
          ) : (
            <div className="space-y-1.5">
              {nodeEdges.map((e) => {
                const d = e.data as MemEdgeData | undefined;
                const otherId = e.source === nodeId ? e.target : e.source;
                const other = labelOf(otherId);
                const manual = d?.kind === "manual";
                return (
                  <div
                    key={e.id}
                    className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2 py-1.5"
                  >
                    <span className="min-w-0 flex-1 truncate text-[12px] text-zinc-200" title={other}>
                      {other}
                    </span>
                    <span
                      className={`shrink-0 rounded-full border px-1.5 py-px text-[10px] font-medium ${
                        manual
                          ? "border-accent/30 bg-accent/10 text-accent-soft"
                          : "border-white/10 bg-white/[0.03] text-zinc-400"
                      }`}
                    >
                      {manual ? "manual" : `similar ${(d?.weight ?? 0).toFixed(2)}`}
                    </span>
                    <button
                      type="button"
                      onClick={() => onDisconnect(e)}
                      aria-label={`Disconnect from ${other}`}
                      title="Disconnect"
                      className="shrink-0 rounded-md border border-white/10 p-1 text-zinc-500 transition-colors hover:border-rose-500/40 hover:text-rose-300"
                    >
                      <Unlink size={12} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <footer className="border-t hairline p-3">
        <button
          type="button"
          onClick={onToggleLink}
          className={`flex w-full items-center justify-center gap-2 rounded-xl border px-3 py-2 text-sm font-medium transition-colors ${
            linking
              ? "border-amber-500/30 bg-amber-500/[0.08] text-amber-200 hover:bg-amber-500/[0.14]"
              : "border-accent/30 bg-accent/[0.08] text-accent-soft hover:bg-accent/[0.14]"
          }`}
        >
          <Link2 size={15} />
          {linking ? "Click another node… (cancel)" : "Connect to…"}
        </button>
      </footer>
    </div>
  );
}

/* -------------------------------------------------------------------------- */

function GraphCanvas() {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [booted, setBooted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<{ embedder: string; note?: string } | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [linkingFrom, setLinkingFrom] = useState<string | null>(null);
  const [hint, setHint] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const hintTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { fitView } = useReactFlow();

  const flashHint = useCallback((text: string, tone: "info" | "error" = "info") => {
    setHint({ text, tone });
    if (hintTimer.current) clearTimeout(hintTimer.current);
    hintTimer.current = setTimeout(() => setHint(null), 5000);
  }, []);
  useEffect(
    () => () => {
      if (hintTimer.current) clearTimeout(hintTimer.current);
    },
    [],
  );

  /* ---- Load / refresh ----------------------------------------------------- */

  const fetchGraph = useCallback(async () => {
    setLoading(true);
    setError(null);
    setOffline(false);
    try {
      const res = await get<GraphDto>("/memory/graph?threshold=0.45");
      const dtoNodes = Array.isArray(res.nodes) ? res.nodes : [];
      const dtoEdges = Array.isArray(res.edges) ? res.edges : [];
      setInfo({ embedder: res.embedder, note: res.note });
      setNodes((cur) =>
        layoutNodes(dtoNodes, new Map(cur.map((n) => [n.id, n.position]))),
      );
      const seen = new Set<string>();
      const built: Edge[] = [];
      for (const e of dtoEdges) {
        const edge = buildEdge(e);
        if (seen.has(edge.id)) continue; // canonicalize duplicate directions
        seen.add(edge.id);
        built.push(edge);
      }
      setEdges(built);
      const ids = new Set(dtoNodes.map((n) => n.id));
      setSelectedNodeId((cur) => (cur && ids.has(cur) ? cur : null));
      setLinkingFrom(null);
      setTimeout(() => fitView({ padding: 0.25, duration: 420 }), 80);
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setOffline(true);
      else setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
      setBooted(true);
    }
  }, [setNodes, setEdges, fitView]);

  useEffect(() => {
    void fetchGraph();
  }, [fetchGraph]);

  /* ---- Link / unlink ------------------------------------------------------ */

  const doLink = useCallback(
    async (a: string, b: string) => {
      if (!a || !b || a === b) return;
      try {
        const res = await post<{ linked: boolean; note?: string }>("/memory/graph/link", { a, b });
        // A manual link supersedes any existing similarity edge for the pair.
        setEdges((eds) => [
          ...eds.filter((e) => e.id !== edgeId(a, b)),
          buildEdge({ a, b, weight: 1, kind: "manual" }),
        ]);
        if (res?.note) flashHint(res.note);
      } catch (err) {
        flashHint(
          err instanceof ApiError && err.status === 0
            ? "Daemon offline — link not saved."
            : `Link failed: ${err instanceof ApiError ? err.message : String(err)}`,
          "error",
        );
      }
    },
    [setEdges, flashHint],
  );

  /** POST the unlink. `alreadyRemoved` = xyflow already dropped it locally
   *  (Delete key); on failure we restore it so the UI never lies. */
  const unlinkOnServer = useCallback(
    async (edge: Edge, alreadyRemoved: boolean) => {
      const d = edge.data as MemEdgeData | undefined;
      const a = d?.a ?? edge.source;
      const b = d?.b ?? edge.target;
      try {
        const res = await post<{ removed: "manual" | "auto"; blocked: boolean }>(
          "/memory/graph/unlink",
          { a, b },
        );
        if (!alreadyRemoved) setEdges((eds) => eds.filter((e) => e.id !== edge.id));
        if (res?.removed === "auto")
          flashHint("Similarity edge blocked — it won't come back.");
      } catch (err) {
        if (alreadyRemoved)
          setEdges((eds) => (eds.some((e) => e.id === edge.id) ? eds : [...eds, edge]));
        flashHint(
          err instanceof ApiError && err.status === 0
            ? "Daemon offline — the link is still there."
            : `Disconnect failed: ${err instanceof ApiError ? err.message : String(err)}`,
          "error",
        );
      }
    },
    [setEdges, flashHint],
  );

  /* ---- Canvas interactions ------------------------------------------------ */

  const onConnect = useCallback(
    (c: Connection) => {
      if (c.source && c.target) void doLink(c.source, c.target);
    },
    [doLink],
  );

  // Delete/Backspace on a selected edge: xyflow removed it locally already.
  const onEdgesDelete = useCallback(
    (deleted: Edge[]) => {
      for (const e of deleted) void unlinkOnServer(e, true);
    },
    [unlinkOnServer],
  );

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => {
      if (linkingFrom) {
        if (node.id !== linkingFrom) void doLink(linkingFrom, node.id);
        setLinkingFrom(null);
        return;
      }
      setSelectedNodeId(node.id);
    },
    [linkingFrom, doLink],
  );

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null);
    setLinkingFrom(null);
  }, []);

  // Esc cancels a pending Connect to…
  useEffect(() => {
    if (!linkingFrom) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLinkingFrom(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [linkingFrom]);

  /* ---- Derived render state ------------------------------------------------ */

  const labelOf = useCallback(
    (id: string) => {
      const n = nodes.find((x) => x.id === id);
      return n ? (n.data as MemNodeData).label : id;
    },
    [nodes],
  );

  const selectedNode = nodes.find((n) => n.id === selectedNodeId);
  const selectedEdge = edges.find((e) => e.selected);
  const selEdgeData = selectedEdge?.data as MemEdgeData | undefined;

  const nodeEdges = useMemo(
    () =>
      selectedNodeId
        ? edges.filter((e) => e.source === selectedNodeId || e.target === selectedNodeId)
        : [],
    [edges, selectedNodeId],
  );

  // Boost the selected edge so "click to select, then disconnect" reads clearly.
  const rfEdges = useMemo(
    () =>
      edges.map((e) =>
        e.selected ? { ...e, style: { ...e.style, strokeWidth: 3, opacity: 1 } } : e,
      ),
    [edges],
  );

  const miniColor = useCallback(
    (node: Node) => groupMeta((node.data as MemNodeData).group).hex,
    [],
  );

  const deselectEdges = useCallback(
    () => setEdges((eds) => eds.map((e) => (e.selected ? { ...e, selected: false } : e))),
    [setEdges],
  );

  const empty = booted && !offline && !error && nodes.length === 0;
  const showCanvas = booted && !offline && !error && nodes.length > 0;

  return (
    <div className="card-surface flex h-[calc(100vh-16rem)] min-h-[520px] flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 border-b hairline px-4 py-3">
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-accent/30 bg-accent/10 text-accent-soft">
            <Waypoints size={16} />
          </span>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-zinc-100">Memory graph</div>
            <div className="truncate text-[11px] text-zinc-500">
              {showCanvas
                ? `${nodes.length} item${nodes.length === 1 ? "" : "s"} · ${edges.length} link${
                    edges.length === 1 ? "" : "s"
                  } — drag between nodes to connect; select a link and press Delete to disconnect.`
                : "Every remembered item, connected by similarity."}
            </div>
          </div>
        </div>
        <button type="button" onClick={() => void fetchGraph()} disabled={loading} className="btn-ghost">
          <RefreshCw size={15} className={loading ? "animate-spin-slow" : ""} /> Refresh
        </button>
      </div>

      {/* Body */}
      {!booted ? (
        <div className="flex-1 p-4">
          <SkeletonRows rows={6} />
        </div>
      ) : offline ? (
        <div className="flex-1 p-4">
          <OfflineHint detail="the memory graph needs the daemon." />
        </div>
      ) : error ? (
        <div className="flex-1 space-y-3 p-4">
          <ErrorNote>Couldn’t load the memory graph: {error}</ErrorNote>
          <button type="button" onClick={() => void fetchGraph()} className="btn-ghost">
            <RefreshCw size={15} /> Retry
          </button>
        </div>
      ) : empty ? (
        <div className="grid flex-1 place-items-center">
          <Empty icon={<Waypoints size={28} />}>
            Nothing remembered yet — lessons, working memory and long-term notes appear
            here as they accumulate.
          </Empty>
        </div>
      ) : (
        <div className="relative flex-1">
          <ReactFlow
            nodes={nodes}
            edges={rfEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            onEdgesDelete={onEdgesDelete}
            nodeTypes={nodeTypes}
            defaultEdgeOptions={defaultEdgeOptions}
            connectionLineStyle={{ stroke: "#22d3ee", strokeWidth: 2 }}
            colorMode="dark"
            fitView
            fitViewOptions={{ padding: 0.25 }}
            minZoom={0.15}
            maxZoom={1.75}
            deleteKeyCode={["Backspace", "Delete"]}
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

          {/* Connect-mode banner */}
          {linkingFrom && (
            <div className="absolute left-1/2 top-3 z-30 flex -translate-x-1/2 items-center gap-2 rounded-xl border border-accent/30 bg-ink-900/95 px-3 py-1.5 text-xs text-accent-soft shadow-card">
              <Link2 size={13} className="shrink-0" />
              <span className="max-w-[320px] truncate">
                Connecting from <b className="font-semibold">{labelOf(linkingFrom)}</b> — click
                another node
              </span>
              <button
                type="button"
                onClick={() => setLinkingFrom(null)}
                className="rounded-md border border-white/10 px-1.5 py-0.5 text-[11px] text-zinc-400 transition-colors hover:border-white/20 hover:text-zinc-200"
              >
                Esc
              </button>
            </div>
          )}

          {/* Selected-edge action bar */}
          {selectedEdge && !linkingFrom && (
            <div className="absolute left-1/2 top-3 z-30 flex -translate-x-1/2 items-center gap-2.5 rounded-xl border border-white/10 bg-ink-900/95 px-3 py-1.5 text-xs text-zinc-300 shadow-card">
              <span className="max-w-[300px] truncate">
                {labelOf(selectedEdge.source)} ↔ {labelOf(selectedEdge.target)}
              </span>
              <span
                className={`shrink-0 rounded-full border px-1.5 py-px text-[10px] font-medium ${
                  selEdgeData?.kind === "manual"
                    ? "border-accent/30 bg-accent/10 text-accent-soft"
                    : "border-white/10 bg-white/[0.03] text-zinc-400"
                }`}
              >
                {selEdgeData?.kind === "manual"
                  ? "manual"
                  : `similar · ${(selEdgeData?.weight ?? 0).toFixed(2)}`}
              </span>
              <button
                type="button"
                onClick={() => void unlinkOnServer(selectedEdge, false)}
                className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-rose-500/25 bg-rose-500/[0.08] px-2 py-1 font-medium text-rose-200 transition-colors hover:bg-rose-500/[0.14]"
              >
                <Unlink size={12} /> Disconnect
              </button>
              <button
                type="button"
                onClick={deselectEdges}
                aria-label="Dismiss"
                className="rounded-md border border-white/10 p-1 text-zinc-500 transition-colors hover:border-white/20 hover:text-zinc-200"
              >
                <X size={12} />
              </button>
            </div>
          )}

          {/* Transient hint (blocked similarity edge, mock-embedder note, errors) */}
          {hint && (
            <div
              role="status"
              aria-live="polite"
              className={`absolute bottom-3 left-1/2 z-30 -translate-x-1/2 rounded-xl border px-3 py-2 text-xs shadow-card ${
                hint.tone === "error"
                  ? "border-rose-500/30 bg-rose-500/[0.12] text-rose-200"
                  : "border-accent/30 bg-ink-900/95 text-accent-soft"
              }`}
            >
              {hint.text}
            </div>
          )}

          {selectedNode && (
            <NodePanel
              nodeId={selectedNode.id}
              data={selectedNode.data as MemNodeData}
              nodeEdges={nodeEdges}
              labelOf={labelOf}
              linking={linkingFrom === selectedNode.id}
              onToggleLink={() =>
                setLinkingFrom((cur) => (cur === selectedNode.id ? null : selectedNode.id))
              }
              onDisconnect={(e) => void unlinkOnServer(e, false)}
              onClose={() => setSelectedNodeId(null)}
            />
          )}
        </div>
      )}

      {/* Legend + honesty footer */}
      {showCanvas && (
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 border-t hairline px-4 py-2">
          <div className="flex flex-wrap items-center gap-x-3.5 gap-y-1 text-[11px] text-zinc-500">
            {(Object.keys(GROUP_META) as MemGroup[]).map((g) => (
              <span key={g} className="inline-flex items-center gap-1.5">
                <span className={`h-2 w-2 rounded-full ${GROUP_META[g].dot}`} />
                {GROUP_META[g].label}
              </span>
            ))}
            <span className="inline-flex items-center gap-1.5">
              <svg width="22" height="6" aria-hidden="true">
                <line
                  x1="1"
                  y1="3"
                  x2="21"
                  y2="3"
                  stroke="#67e8f9"
                  strokeOpacity="0.55"
                  strokeWidth="1.5"
                  strokeDasharray="5 3"
                />
              </svg>
              similar
            </span>
            <span className="inline-flex items-center gap-1.5">
              <svg width="22" height="6" aria-hidden="true">
                <line x1="1" y1="3" x2="21" y2="3" stroke="#22d3ee" strokeWidth="2" />
              </svg>
              manual
            </span>
          </div>
          {info && (
            <div className="text-[11px] text-zinc-500">
              edges scored by <span className="font-mono text-zinc-400">{info.embedder}</span>
              {info.note ? <span className="text-amber-300/70"> — {info.note}</span> : null}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function MemoryGraph() {
  // ReactFlowProvider gives us useReactFlow() (fitView) inside <GraphCanvas/>.
  return (
    <ReactFlowProvider>
      <GraphCanvas />
    </ReactFlowProvider>
  );
}
