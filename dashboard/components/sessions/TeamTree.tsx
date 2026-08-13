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
import { FileText, Network } from "lucide-react";
import { get } from "@/lib/api";
import type { SessionView } from "@/lib/types";
import { Card, Badge } from "@/components/ui";
import AgentFace, { moodForStatus } from "@/components/agents/AgentFace";
import { shortId } from "@/lib/format";
import { basename } from "@/components/chat/ArtifactsRail";
import {
  sessionFileRows,
  type SessionFileRow,
  type SessionResult,
} from "@/components/sessions/SessionFiles";

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

/** Per-child file handover (v1.168.0, P2): rows are that child's OWN ledger
 *  result, `total` the honest server-side count (the lists are capped). */
export interface ChildFiles {
  rows: SessionFileRow[];
  total: number;
}

/** How many file chips a tree node shows before "+N more" takes over — a
 *  delegate that wrote 40 files must not turn the tree into a wall. */
const CHIPS_SHOWN = 5;

function FileChips({
  files,
  onPreviewFile,
}: {
  files: ChildFiles;
  onPreviewFile?: (path: string) => void;
}) {
  const shown = files.rows.slice(0, CHIPS_SHOWN);
  const more = files.total - shown.length;
  const chipClass =
    "inline-flex max-w-[16rem] items-center gap-1 rounded-md border " +
    "border-white/[0.08] bg-white/[0.03] px-1.5 py-0.5 font-mono " +
    "text-[10.5px] text-zinc-400";
  return (
    <div
      className="mt-1 flex flex-wrap items-center gap-1.5"
      data-testid="team-files"
    >
      <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-zinc-600">
        wrote
      </span>
      {shown.map((f) =>
        onPreviewFile ? (
          <button
            key={f.path}
            type="button"
            onClick={() => onPreviewFile(f.path)}
            title={f.path}
            className={`${chipClass} transition-colors hover:border-accent/40 hover:text-accent-soft`}
          >
            <FileText size={10} className="shrink-0" aria-hidden />
            <span className="truncate">{basename(f.path)}</span>
          </button>
        ) : (
          <span key={f.path} title={f.path} className={chipClass}>
            <FileText size={10} className="shrink-0" aria-hidden />
            <span className="truncate">{basename(f.path)}</span>
          </span>
        ),
      )}
      {more > 0 && (
        <span className="text-[10px] text-zinc-600">+{more} more</span>
      )}
    </div>
  );
}

function Branch({
  nodes,
  depth,
  files,
  onPreviewFile,
}: {
  nodes: TeamNode[];
  depth: number;
  files: Record<string, ChildFiles>;
  onPreviewFile?: (path: string) => void;
}) {
  return (
    <div className={depth > 0 ? "ml-4 border-l border-white/[0.07] pl-3" : ""}>
      {nodes.map((n) => (
        <div key={n.session.id} className="py-1">
          <div className="flex flex-wrap items-center gap-2">
            {/* The delegate's face (v1.171.0): identity from its agent type,
                mood from the session's REAL status — moodForStatus is the one
                shared status→mood mapping, never a local guess. title="" keeps
                the SVG <title> out: the agent's name is the link RIGHT NEXT to
                the face, and a duplicate text node would double every
                get-by-text on this panel. The aria-hidden wrapper finishes the
                decorative mode: title="" alone left role="img" with an EMPTY
                aria-label, an invalid ARIA state screen readers handle
                unpredictably. */}
            <span aria-hidden="true" className="contents">
              <AgentFace
                name={n.session.agent_type}
                mood={moodForStatus(n.session.status)}
                size={18}
                title=""
              />
            </span>
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
          {files[n.session.id] && (
            <FileChips
              files={files[n.session.id]}
              onPreviewFile={onPreviewFile}
            />
          )}
          {n.children.length > 0 && (
            <Branch
              nodes={n.children}
              depth={depth + 1}
              files={files}
              onPreviewFile={onPreviewFile}
            />
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
  onPreviewFile,
}: {
  sessionId: string;
  active: boolean;
  /** When provided, each child's file chips become preview buttons (v1.168.0).
   *  Absent → chips render as plain, titled labels, never dead buttons. */
  onPreviewFile?: (path: string) => void;
}) {
  const [team, setTeam] = useState<TeamResponse | null>(null);
  const [files, setFiles] = useState<Record<string, ChildFiles>>({});

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const t = await get<TeamResponse>(`/sessions/${sessionId}/team`);
        if (!alive) return;
        setTeam(t);
        const kids = t?.children ?? [];
        if (kids.length === 0) return;
        // Each delegate's OWN ledger result — "child A wrote the workbook" is
        // only honest when the files hang off the child that journaled them.
        // The tree itself never depends on these fetches, and the map MERGES
        // rather than replaces: a child whose fetch fails on one poll keeps
        // its last-good chips ("absent beats wrong" is for data never loaded,
        // not for discarding data already shown — the same rule the team
        // fetch above follows by keeping the old tree). Only a SUCCESSFUL
        // response saying "no files" clears a child's entry.
        const entries = await Promise.all(
          kids.map(
            async (
              c,
            ): Promise<readonly [string, ChildFiles | null] | null> => {
              try {
                const r = await get<SessionResult>(`/sessions/${c.id}/result`);
                if (!r?.found) return [c.id, null] as const;
                const rows = sessionFileRows(r, c.workspace_path ?? "");
                if (rows.length === 0) return [c.id, null] as const;
                const total =
                  (r.files_created_total ?? 0) + (r.files_changed_total ?? 0);
                return [c.id, { rows, total: total || rows.length }] as const;
              } catch {
                // Transient failure — keep whatever this child showed before.
                return null;
              }
            },
          ),
        );
        if (alive) {
          setFiles((prev) => {
            const next = { ...prev };
            for (const e of entries) {
              if (e === null) continue; // errored fetch: last-good stays
              const [childId, val] = e;
              if (val === null) delete next[childId];
              else next[childId] = val;
            }
            return next;
          });
        }
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
        <Branch
          nodes={tree}
          depth={0}
          files={files}
          onPreviewFile={onPreviewFile}
        />
      </div>
    </Card>
  );
}
