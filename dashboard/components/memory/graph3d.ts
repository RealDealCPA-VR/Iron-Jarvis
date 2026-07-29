// Pure helpers for the 3D memory graph (v1.115.0) — zero React, zero three.js,
// so the mapping, colors, and (critically) the tooltip HTML are unit-testable.
// The tooltip matters most: react-force-graph renders nodeLabel as RAW HTML,
// and memory text is USER/AGENT-written content — an unescaped snippet would
// let a remembered note inject markup into the page. Everything user-sourced
// passes through escapeHtml before it touches a tag.

export type MemGroup = "lesson" | "memory" | "note";
export type EdgeKind = "manual" | "auto";

export interface GraphNodeDto {
  id: string;
  label: string;
  group: MemGroup;
  snippet: string;
  meta?: Record<string, unknown>;
}
export interface GraphEdgeDto {
  a: string;
  b: string;
  weight: number;
  kind: EdgeKind;
}
export interface GraphDto {
  nodes: GraphNodeDto[];
  edges: GraphEdgeDto[];
  embedder: string;
  note?: string;
}

/** A node as react-force-graph-3d consumes it (positions added by the lib). */
export interface Node3D {
  id: string;
  label: string;
  group: MemGroup;
  snippet: string;
  x?: number;
  y?: number;
  z?: number;
}
export interface Link3D {
  source: string | Node3D;
  target: string | Node3D;
  kind: EdgeKind;
  weight: number;
}

/** Group palette — the same tones the memory scopes wear everywhere else
 *  (lessons amber, working memory cyan/accent, long-term notes emerald).
 *  `bright` is the selected/linking variant: same hue, lifted lightness, so a
 *  highlighted node reads as "this one, lit up", not a different category. */
export const GROUP_3D: Record<
  MemGroup,
  { label: string; hex: string; bright: string }
> = {
  lesson: { label: "lesson", hex: "#fbbf24", bright: "#fde68a" },
  memory: { label: "memory", hex: "#22d3ee", bright: "#a5f3fc" },
  note: { label: "note", hex: "#34d399", bright: "#a7f3d0" },
};

export function normGroup(g: unknown): MemGroup {
  return g === "lesson" || g === "note" ? (g as MemGroup) : "memory";
}

export function toGraphData(dto: GraphDto): { nodes: Node3D[]; links: Link3D[] } {
  const ids = new Set(dto.nodes.map((n) => n.id));
  return {
    nodes: dto.nodes.map((n) => ({
      id: n.id,
      label: n.label || n.id,
      group: normGroup(n.group),
      snippet: n.snippet || "",
    })),
    // A dangling edge (node trimmed by the per-group cap) would crash the
    // force engine — drop them here rather than trusting the server cap math.
    links: dto.edges
      .filter((e) => ids.has(e.a) && ids.has(e.b))
      .map((e) => ({ source: e.a, target: e.b, kind: e.kind, weight: e.weight })),
  };
}

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Hover tooltip — the whole point of hiding always-on labels. Dark chip,
 *  group-tinted title, snippet clipped server-side. All content escaped. */
export function tooltipHtml(n: Node3D): string {
  const g = GROUP_3D[n.group];
  const title = escapeHtml(n.label);
  const snippet = escapeHtml(n.snippet).slice(0, 400);
  return (
    `<div style="max-width:280px;background:rgba(9,11,14,0.94);border:1px solid rgba(255,255,255,0.1);` +
    `border-radius:10px;padding:8px 10px;font:12px/1.45 'Segoe UI',system-ui,sans-serif;color:#d6dde3;">` +
    `<div style="color:${g.hex};font-weight:600;margin-bottom:2px;">${title}` +
    `<span style="color:#5f6b76;font-weight:400;"> · ${g.label}</span></div>` +
    (snippet ? `<div style="color:#93a0ac;">${snippet}</div>` : "") +
    `</div>`
  );
}

/** Hover label for an edge: the user's own links say so; similarity says how
 *  strong. (Escaping unneeded — both branches are app-built strings.) */
export function linkTooltip(l: Link3D): string {
  return l.kind === "manual"
    ? "your link — click to remove"
    : `similarity ${l.weight.toFixed(2)} — click to remove`;
}

export function nodeColorFor(
  n: Node3D,
  selectedId: string | null,
  linkFromId: string | null,
): string {
  const g = GROUP_3D[n.group];
  return n.id === selectedId || n.id === linkFromId ? g.bright : g.hex;
}

/** Whether the graph is allowed to delete this node. Long-term notes are FILES
 *  in the user's own memory base (vault / Notion / drive) — a canvas click
 *  must never reach into those; the caller shows where to manage it instead. */
export function deletableKind(id: string): { ok: boolean; base?: string } {
  if (id.startsWith("lesson:") || id.startsWith("wm:")) return { ok: true };
  if (id.startsWith("ltm:")) return { ok: false, base: id.split(":", 3)[1] };
  return { ok: false };
}
