// "If" and "For each" on the workflow canvas (idea 09, phase 09-a). Pure: no
// React. The engine (workflows/engine.py, workflows/logic.py) decides and
// repeats; this file only says what a block is, in words, and where its
// boxes sit on the canvas.
//
// A block is a CONTAINER node (type "each" or "if") and the steps inside it
// are child nodes (`parentId`): a For each has one row of steps, an If two
// lanes, Then and Otherwise. A child carries `branch` ("body" | "then" |
// "else") and `order` (its place in that list); the serializer reads those,
// never the boxes' positions, so moving a box can never reorder a workflow.
// (Dragging steps in and out of a block is phase 09-b.)

import type { Edge, Node } from "@xyflow/react";

export const BLOCK_KINDS = ["if", "for_each"] as const;
export type BlockKind = (typeof BLOCK_KINDS)[number];

/** The canvas node type of each block kind. */
export const BLOCK_NODE_TYPE: Record<BlockKind, "if" | "each"> = { if: "if", for_each: "each" };

export function isBlockKind(kind: unknown): kind is BlockKind {
  return kind === "if" || kind === "for_each";
}

export function isBlockNode(n: Pick<Node, "type"> | undefined | null): boolean {
  return !!n && (n.type === "if" || n.type === "each");
}

/** The words on screen for each block (VOCABULARY.md: If, For each). */
export const BLOCK_META: Record<BlockKind, { label: string; blurb: string; chip: string }> = {
  if: {
    label: "If",
    blurb: "Checks a value from an earlier step, then runs Then or Otherwise. Iron Jarvis works it out itself, the same way every time.",
    chip: "border-tone-violet/30 bg-tone-violet/10 text-tone-violet",
  },
  for_each: {
    label: "For each",
    blurb: "Runs the steps inside once for every file in a folder, or every item of a list an earlier step found.",
    chip: "border-tone-info/30 bg-tone-info/10 text-tone-info",
  },
};

/** The If's two lanes, as they are said. */
export const THEN_WORD = "Then";
export const OTHERWISE_WORD = "Otherwise";

/** D-09.1: how many items a For each works through unless told otherwise. */
export const DEFAULT_MAX_ITEMS = 200;
export const MAX_ITEMS_CAP = 2000;
/** Over a folder: the most one listing of a folder shows (validate.FOLDER_ITEMS_CAP). */
export const FOLDER_ITEMS_CAP = 1000;
export const MAX_PARALLEL = 4;

/** D-09.1: said wherever a For each holds an Agent step (the chat's card and
 *  the canvas's panel). */
export const EACH_COST_LINE =
  "Each Agent step inside runs once for every item: one model run per item.";

export interface ConditionLeaf {
  left?: string;
  op?: string;
  right?: string;
}
export type Condition = ConditionLeaf | { all: ConditionLeaf[] } | { any: ConditionLeaf[] };

export interface Over {
  from?: string;
  files?: { folder?: string; glob?: string };
}

/** Every comparison, in the engine's order (workflows/logic.OPS), in words. */
export const OP_OPTIONS: { key: string; label: string; oneSided?: boolean; numbers?: boolean }[] = [
  { key: "is", label: "is" },
  { key: "is_not", label: "is not" },
  { key: "contains", label: "contains" },
  { key: "over", label: "is over", numbers: true },
  { key: "under", label: "is under", numbers: true },
  { key: "at_least", label: "is at least", numbers: true },
  { key: "at_most", label: "is at most", numbers: true },
  { key: "empty", label: "is empty", oneSided: true },
  { key: "not_empty", label: "is not empty", oneSided: true },
  { key: "matches", label: "matches (use * for any letters)" },
];

const OP_WORDS: Record<string, string> = {
  is: "is",
  is_not: "is not",
  contains: "contains",
  over: "is over",
  under: "is under",
  at_least: "is at least",
  at_most: "is at most",
  empty: "is empty",
  not_empty: "is not empty",
  matches: "matches",
};

export function isOneSided(op: string | undefined): boolean {
  return op === "empty" || op === "not_empty";
}

/** The leaf checks of a condition, and how they are joined. */
export function conditionLeaves(cond: unknown): { join: "all" | "any" | null; leaves: ConditionLeaf[] } {
  if (!cond || typeof cond !== "object" || Array.isArray(cond)) return { join: null, leaves: [] };
  const c = cond as Record<string, unknown>;
  for (const join of ["all", "any"] as const) {
    if (Array.isArray(c[join])) {
      return {
        join,
        leaves: (c[join] as unknown[]).filter(
          (x): x is ConditionLeaf => !!x && typeof x === "object" && !Array.isArray(x),
        ),
      };
    }
  }
  return { join: null, leaves: [c as ConditionLeaf] };
}

/** The condition as written: "{{Read total}} is over 5000". */
export function describeCondition(cond: unknown): string {
  const { join, leaves } = conditionLeaves(cond);
  const one = (l: ConditionLeaf) => {
    const words = OP_WORDS[String(l.op ?? "")] ?? String(l.op ?? "");
    const left = String(l.left ?? "").trim();
    if (isOneSided(l.op)) return `${left} ${words}`.trim();
    return `${left} ${words} ${String(l.right ?? "").trim()}`.trim();
  };
  const said = leaves.map(one).filter(Boolean);
  if (!said.length) return "";
  return said.join(join === "any" ? " or " : " and ");
}

/** What a For each works through: "every *.pdf in C:/Intake". */
export function describeOver(over: unknown): string {
  const o = (over && typeof over === "object" ? over : {}) as Over;
  if (o.files && typeof o.files === "object") {
    const folder = String(o.files.folder ?? "").trim() || "a folder";
    const glob = String(o.files.glob ?? "").trim();
    return glob && glob !== "*" ? `every ${glob} in ${folder}` : `every file in ${folder}`;
  }
  if (typeof o.from === "string" && o.from.trim()) return `every item of ${o.from.trim()}`;
  return "";
}

/** Whether a list of steps holds an Agent step, inside blocks too (the cost
 *  line, D-09.1). */
export function hasAgentInside(steps: unknown): boolean {
  if (!Array.isArray(steps)) return false;
  return steps.some((s) => {
    if (!s || typeof s !== "object") return false;
    const st = s as { kind?: string; steps?: unknown; else_steps?: unknown };
    const kind = st.kind || "agent";
    if (kind === "agent") return true;
    return hasAgentInside(st.steps) || hasAgentInside(st.else_steps);
  });
}

/** Whether any For each in a list of steps, at any depth (inside an If's
 *  lane too), holds an Agent step: the card's cost line (D-09.1). */
export function eachHoldsAgent(steps: unknown): boolean {
  if (!Array.isArray(steps)) return false;
  return steps.some((s) => {
    if (!s || typeof s !== "object") return false;
    const st = s as { kind?: string; steps?: unknown; else_steps?: unknown };
    if (st.kind === "for_each" && hasAgentInside(st.steps)) return true;
    return eachHoldsAgent(st.steps) || eachHoldsAgent(st.else_steps);
  });
}

/* ---- Layout: where the boxes sit ---------------------------------------- */

export const STEP_W = 230;
export const STEP_H = 112;
const PAD = 16;
const GAP = 24;
const HEADER = 72;
const LANE_LABEL = 22;
const EMPTY_LANE = 56;

/** The children of a block in one of its lists, in order. */
export function childrenOf(nodes: Node[], parentId: string, branch?: string): Node[] {
  return nodes
    .filter(
      (n) =>
        n.parentId === parentId &&
        (branch === undefined || String((n.data as { branch?: string }).branch ?? "body") === branch),
    )
    .sort(
      (a, b) =>
        Number((a.data as { order?: number }).order ?? 0) -
        Number((b.data as { order?: number }).order ?? 0),
    );
}

/** A node and everything inside it (a block's steps, their blocks' steps). */
export function withDescendants(nodes: Node[], id: string): Set<string> {
  const out = new Set<string>([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const n of nodes) {
      if (n.parentId && out.has(n.parentId) && !out.has(n.id)) {
        out.add(n.id);
        grew = true;
      }
    }
  }
  return out;
}

function nodeSize(n: Node): { w: number; h: number } {
  const d = n.data as { w?: number; h?: number };
  if (isBlockNode(n)) return { w: Number(d.w ?? STEP_W + 2 * PAD), h: Number(d.h ?? HEADER + STEP_H) };
  return { w: STEP_W, h: STEP_H };
}

function depthOf(nodes: Node[], n: Node): number {
  let depth = 0;
  let cur: Node | undefined = n;
  const byId = new Map(nodes.map((x) => [x.id, x]));
  while (cur?.parentId) {
    depth += 1;
    cur = byId.get(cur.parentId);
    if (depth > 16) break;
  }
  return depth;
}

/** Place every block's steps inside it and size every block to fit, deepest
 *  blocks first; renumber each list's `order` and `index` (1, 2, …). A top-
 *  level block that grew pushes the top-level boxes to its right along, so
 *  nothing ends up under it. Pure: returns new nodes. */
export function layoutBlocks(input: Node[]): Node[] {
  let nodes = input.map((n) => ({ ...n, data: { ...n.data } }));
  const blocks = nodes
    .filter(isBlockNode)
    .sort((a, b) => depthOf(nodes, b) - depthOf(nodes, a));
  const grewBy = new Map<string, number>();
  for (const block of blocks) {
    const d = block.data as Record<string, unknown>;
    const lanes = block.type === "if" ? (["then", "else"] as const) : (["body"] as const);
    let y = HEADER;
    let width = STEP_W + 2 * PAD;
    const laneTops: Record<string, number> = {};
    const laneHeights: Record<string, number> = {};
    const laneCounts: Record<string, number> = {};
    for (const lane of lanes) {
      if (block.type === "if") y += LANE_LABEL;
      laneTops[lane] = y;
      const kids = childrenOf(nodes, block.id, lane);
      laneCounts[lane] = kids.length;
      let x = PAD;
      let rowH = kids.length ? 0 : EMPTY_LANE;
      kids.forEach((kid, k) => {
        const size = nodeSize(kid);
        kid.position = { x, y };
        kid.data = { ...kid.data, order: k, index: k + 1, branch: lane };
        x += size.w + GAP;
        rowH = Math.max(rowH, size.h);
      });
      width = Math.max(width, x - GAP + PAD);
      laneHeights[lane] = rowH;
      y += rowH + GAP;
    }
    const height = y - GAP + PAD;
    const before = Number(d.w ?? 0);
    block.data = { ...block.data, w: width, h: height, laneTops, laneHeights, laneCounts };
    block.style = { ...(block.style ?? {}), width, height };
    if (!block.parentId && before && width > before) grewBy.set(block.id, width - before);
  }
  for (const [id, delta] of grewBy) {
    const block = nodes.find((n) => n.id === id);
    if (!block) continue;
    nodes = nodes.map((n) =>
      !n.parentId && n.id !== id && n.position.x > block.position.x
        ? { ...n, position: { x: n.position.x + delta, y: n.position.y } }
        : n,
    );
  }
  return nodes;
}

/** The thin arrows between the steps of one list inside a block. Drawn for
 *  the eye only: nothing can select, delete or attach to them. */
export function innerEdges(nodes: Node[]): Edge[] {
  const out: Edge[] = [];
  for (const block of nodes.filter(isBlockNode)) {
    const lanes = block.type === "if" ? ["then", "else"] : ["body"];
    for (const lane of lanes) {
      const kids = childrenOf(nodes, block.id, lane);
      for (let k = 1; k < kids.length; k += 1) {
        out.push({
          id: `inner:${kids[k - 1].id}->${kids[k].id}`,
          source: kids[k - 1].id,
          target: kids[k].id,
          selectable: false,
          deletable: false,
          focusable: false,
          data: { inner: true },
        });
      }
    }
  }
  return out;
}

export function isInnerEdge(e: Pick<Edge, "data">): boolean {
  return !!(e.data as { inner?: boolean } | undefined)?.inner;
}

/* ---- A run's view of a block -------------------------------------------- */

export interface ItemRow {
  n: number;
  label: string;
  status: string;
  summary: string;
}

/** The per-item rows a For each's output carries (`data`), in order. */
export function itemRows(out: unknown): ItemRow[] {
  if (!out || typeof out !== "object") return [];
  const data = (out as { data?: unknown }).data;
  if (!Array.isArray(data)) return [];
  return data
    .filter((r): r is Record<string, unknown> => !!r && typeof r === "object")
    .map((r) => ({
      n: Number(r.n ?? 0),
      label: String(r.label ?? ""),
      status: String(r.status ?? ""),
      summary: String(r.summary ?? ""),
    }))
    .filter((r) => r.n > 0)
    .sort((a, b) => a.n - b.n);
}

/** Which lane an If took, in words ("Then" / "Otherwise"), or "". */
export function tookWords(out: unknown): string {
  const took = out && typeof out === "object" ? (out as { took?: string }).took : undefined;
  return took === "then" ? THEN_WORD : took === "else" ? OTHERWISE_WORD : "";
}

/** A block's status for a run that has ENDED: the engine never leaves one
 *  "running" on a finished run, but an older record may; it reads Stopped. */
export function blockStatus(status: string | undefined, runEnded: boolean): string {
  const s = String(status ?? "");
  return runEnded && s === "running" ? "cancelled" : s;
}
