"use client";

// Team tree (v1.166.0, B3) — who is working under this session.
//
// A supervisor/planner that delegates spawns CHILD sessions; before this panel
// the only trace on the parent's page was a `parent_id` column buried in the
// runs table, and the child sessions themselves were invisible unless you knew
// to trawl /sessions. This renders `GET /sessions/{id}/team` (shape frozen in
// the v1.166.0 plan): the parent's runs → child runs whose parent_id matches →
// those runs' sessions, to delegation depth 3.
//
// Rendering rules:
//  - Nothing renders unless children exist — a solo session must not grow an
//    empty "Team" box advertising a feature it isn't using.
//  - A child whose parent run we cannot resolve (or that would cycle) attaches
//    at the ROOT instead of being dropped: a visible child in the wrong slot
//    beats a silently missing one.

import { useEffect, useState } from "react";
import Link from "next/link";
import { Network } from "lucide-react";
import { get } from "@/lib/api";
import type { SessionView } from "@/lib/types";
import { Card, Badge } from "@/components/ui";
import { shortId } from "@/lib/format";

/** One run row from the team endpoint (a slice of AgentRun). */
export interface TeamRun {
  id: string;
  session_id: string;
  parent_id: string | null;
  agent_type: string;
  state: string;
}

/** A child session, plus the run id (in the PARENT session) that spawned it. */
export type TeamChild = SessionView & { parent_run_id: string };

export interface TeamResponse {
  found: boolean;
  session_id: string;
  children: TeamChild[];
  runs: TeamRun[];
}

export interface TeamNode {
  session: TeamChild;
  children: TeamNode[];
}

/**
 * Nest children under the session that spawned them. `parent_run_id` names a
 * RUN; `runs` maps runs to their owning session, so parent session =
 * runs[child.parent_run_id].session_id. Pure — the tests drive it directly.
 */
export function buildTeamTree(team: TeamResponse): TeamNode[] {
  const runOwner = new Map<string, string>();
  for (const r of team.runs ?? []) runOwner.set(r.id, r.session_id);

  const byParent = new Map<string, TeamChild[]>();
  for (const c of team.children ?? []) {
    let parent = runOwner.get(c.parent_run_id) ?? team.session_id;
    // A self-parented child would be unreachable from the root — surface it
    // there instead. Losing a delegation silently is the worse failure.
    if (parent === c.id) parent = team.session_id;
    const list = byParent.get(parent) ?? [];
    list.push(c);
    byParent.set(parent, list);
  }

  const placed = new Set<string>();
  const nodesFor = (sessionId: string): TeamNode[] =>
    (byParent.get(sessionId) ?? [])
      .filter((c) => !placed.has(c.id))
      .map((c) => {
        placed.add(c.id);
        return { session: c, children: nodesFor(c.id) };
      });

  const roots = nodesFor(team.session_id);
  // Mutual cycles / detached islands: attach whatever traversal missed at the
  // root, subtrees intact (placed-guard keeps this loop from re-adding).
  for (const c of team.children ?? []) {
    if (placed.has(c.id)) continue;
    placed.add(c.id);
    roots.push({ session: c, children: nodesFor(c.id) });
  }
  return roots;
}

/** Count every node in the tree (for the card header). */
export function teamSize(nodes: TeamNode[]): number {
  let n = 0;
  for (const node of nodes) n += 1 + teamSize(node.children);
  return n;
}

function Branch({ nodes, depth }: { nodes: TeamNode[]; depth: number }) {
  return (
    <div className={depth > 0 ? "ml-4 border-l border-white/[0.07] pl-3" : ""}>
      {nodes.map((n) => (
        <div key={n.session.id} className="py-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={`/sessions/${n.session.id}`}
              className="font-medium text-accent-soft transition-colors hover:text-accent"
            >
              {n.session.agent_type}
            </Link>
            <Badge value={n.session.status} />
            <span className="font-mono text-[11px] text-zinc-600">
              {shortId(n.session.id)}
            </span>
          </div>
          <div className="mt-0.5 line-clamp-1 text-xs text-zinc-500">
            {n.session.task}
          </div>
          {n.children.length > 0 && (
            <Branch nodes={n.children} depth={depth + 1} />
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * The panel. Fetches on mount and re-polls (~8s) while the parent session is
 * active — children appear as the supervisor delegates. Renders NOTHING when
 * the session has no children (or the endpoint is unreachable: an optional
 * panel that can't load should be absent, not an error box).
 */
export function TeamTree({
  sessionId,
  active,
}: {
  sessionId: string;
  active: boolean;
}) {
  const [team, setTeam] = useState<TeamResponse | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const t = await get<TeamResponse>(`/sessions/${sessionId}/team`);
        if (alive) setTeam(t);
      } catch {
        /* optional panel — absent beats wrong */
      }
    };
    void load();
    if (!active) {
      return () => {
        alive = false;
      };
    }
    const timer = setInterval(() => void load(), 8000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [sessionId, active]);

  if (!team?.found) return null;
  const tree = buildTeamTree(team);
  if (tree.length === 0) return null;
  const size = teamSize(tree);

  return (
    <Card
      title={`Team · ${size} agent${size === 1 ? "" : "s"}`}
      icon={<Network size={15} />}
      right={
        <span className="text-[11px] text-zinc-500">delegated sub-sessions</span>
      }
    >
      <div className="text-sm text-zinc-300">
        <Branch nodes={tree} depth={0} />
      </div>
    </Card>
  );
}
