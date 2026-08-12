"use client";

// Roster (v1.139.0) — who can take delegated work. A read-only awareness
// strip over GET /agents/roster: every agent that chat escalation, workflows,
// and the supervisor can hand work to, with HONEST measured stats — a rate
// never renders without its sample count ("87% over 23 runs", never a bare
// "87%"). Older daemons don't serve the endpoint, so the whole section simply
// doesn't exist rather than erroring over a feature the daemon predates.

import { useState } from "react";
import { Briefcase, MessageCircle, WifiOff } from "lucide-react";
import { useApi } from "@/lib/useApi";
import { Card } from "@/components/ui";
import { Reveal } from "@/components/motion";
import {
  AgentAvatar,
  participantKey,
  SOURCE_LABEL,
  type AgentSource,
} from "@/components/agents/identity";

interface RosterStats {
  sessions?: number | null;
  avg_score?: number | null;
  success_rate?: number | null;
  trend?: string | null;
}

/** One GET /agents/roster entry. Typed here (not lib/types.ts — that file is
 *  owned by the coordinating session this release). */
export interface RosterEntry {
  /** "builder" | "custom:<slug>" | "remote:<name>" — the delegation name. */
  name: string;
  kind: AgentSource;
  description: string;
  /** A session can actually be spawned on it (false → chat-only for now). */
  delegable: boolean;
  /** Remotes carry live status; builtin/dynamic are always true. */
  healthy: boolean;
  stats: RosterStats | null;
  /** The daemon's own composed one-liner, stats parenthetical included. */
  line?: string;
}

const KIND_PILL: Record<AgentSource, string> = {
  builtin: "border-accent/30 bg-accent/[0.08] text-accent-soft",
  dynamic: "border-violet-500/25 bg-violet-500/10 text-violet-300",
  remote: "border-zinc-500/25 bg-zinc-500/10 text-zinc-400",
};

/** The shown name: the bare slug — the kind pill carries provenance, so the
 *  wire prefixes ("custom:", "remote:") stay off the screen. */
function bareName(name: string): string {
  if (name.startsWith("custom:")) return name.slice("custom:".length);
  if (name.startsWith("remote:")) return name.slice("remote:".length);
  return name;
}

/** The honest stats text. Prefer the daemon's own wording — the trailing
 *  parenthetical of its composed `line` ("87% over 23 runs", "no runs yet").
 *  "(offline)" is health, not stats — the row already shows an offline pill,
 *  so it falls through to the stats dict. Sample counts are ALWAYS visible;
 *  a percentage never renders bare. */
function statsText(e: RosterEntry): string {
  const paren = /\(([^()]+)\)\s*$/.exec(e.line ?? "")?.[1];
  if (paren && paren.trim().toLowerCase() !== "offline") return paren.trim();
  const s = e.stats;
  const runs = typeof s?.sessions === "number" ? s.sessions : 0;
  if (!s || runs <= 0) return "no runs yet";
  const runsTxt = `${runs} run${runs === 1 ? "" : "s"}`;
  const rate = s.success_rate;
  if (typeof rate !== "number") return `${runsTxt} so far`;
  // success_rate is a FRACTION by contract — improvement/engine.py composes
  // round(success_count / n, 4) and roster.py's own line() renders it * 100.
  // The scale is pinned, so no fraction-vs-percent guessing (1 means 100%,
  // never "1%"); the clamp only guards a corrupt wire value.
  const pct = Math.round(Math.min(1, Math.max(0, rate)) * 100);
  return `${pct}% over ${runsTxt}`;
}

/**
 * The Roster section for the Agents page. Renders nothing while loading, on
 * ANY fetch error (a pre-roster daemon 404s here — hiding beats a scary
 * error), and on an empty roster.
 */
export function RosterStrip({
  onTalk,
  onAssign,
}: {
  /** The Talk button: open (or start) a 1:1 thread with this agent at the
   *  round-table. Only offered for delegable + healthy entries; omit the
   *  prop (older daemons without thread routes) and no button renders. */
  onTalk?: (kind: AgentSource, name: string) => void;
  /** The Give-work button (v1.166.0): preselect this agent in the job-post
   *  card. Same delegable + healthy gate as Talk — a non-delegable entry
   *  (supervisor) stays chat-only, and an offline remote can't take work. */
  onAssign?: (kind: AgentSource, name: string) => void;
} = {}) {
  const [choice, setChoice] = useState("");
  const { data, error } = useApi<{ roster?: RosterEntry[] }>("/agents/roster");
  const entries = (data?.roster ?? []).filter(
    (e): e is RosterEntry => Boolean(e) && typeof e.name === "string",
  );
  if (error || entries.length === 0) return null;

  // The Reveal lives HERE (not at the call site) so a hidden roster leaves no
  // empty wrapper behind to double the page's space-y gap.
  //
  // v1.158.0: a PICKER, not a list. One row per agent meant the section grew
  // with the roster and pushed the actual work down the page; the roster is
  // reference material you consult, not something you read top to bottom. The
  // selected agent gets the full detail the rows used to carry — nothing was
  // dropped, it just stopped all being on screen at once.
  const selected =
    entries.find((e) => e.name === choice) ?? entries[0];
  const offline = selected.kind === "remote" && !selected.healthy;
  const shown = bareName(selected.name);
  const kindLabel = SOURCE_LABEL[selected.kind] ?? (selected.kind || "agent");
  const kindPill = KIND_PILL[selected.kind] ?? KIND_PILL.remote;

  return (
    <Reveal>
      <Card pad={false} className="overflow-hidden">
        <div className="flex items-center justify-between border-b hairline px-4 py-2.5">
          <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
            Roster · {entries.length}
          </span>
          <span className="text-[11px] text-zinc-600">
            who can take delegated work
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2 px-4 pt-3">
          <label className="sr-only" htmlFor="roster-pick">
            Choose an agent
          </label>
          <select
            id="roster-pick"
            value={selected.name}
            onChange={(ev) => setChoice(ev.target.value)}
            className="field min-w-0 flex-1 py-1.5 text-[12.5px]"
          >
            {entries.map((e) => (
              // Provenance and health ride IN the option text: a picker whose
              // closed state hides whether an agent is a remote — or offline —
              // is the wrong trade for a tidier page.
              <option key={e.name} value={e.name}>
                {bareName(e.name)} — {SOURCE_LABEL[e.kind] ?? e.kind}
                {e.kind === "remote" && !e.healthy ? " (offline)" : ""}
                {!e.delegable ? " (chat-only)" : ""}
              </option>
            ))}
          </select>
          {onTalk && selected.delegable && selected.healthy && (
            <button
              type="button"
              onClick={() => onTalk(selected.kind, shown)}
              title={`Talk with ${shown} at the round-table`}
              className="btn-ghost shrink-0 px-2.5 py-1.5 text-[11.5px]"
            >
              <MessageCircle size={12} /> Talk
            </button>
          )}
          {onAssign && selected.delegable && selected.healthy && (
            <button
              type="button"
              onClick={() => onAssign(selected.kind, shown)}
              title={`Give ${shown} a job via the job-post card`}
              className="btn-ghost shrink-0 px-2.5 py-1.5 text-[11.5px]"
            >
              <Briefcase size={12} /> Give work
            </button>
          )}
        </div>

        <div
          className={`flex items-start gap-2.5 px-4 pb-3.5 pt-2.5 ${
            offline ? "opacity-55" : ""
          }`}
        >
          <AgentAvatar
            agentKey={participantKey(selected.kind, shown)}
            name={shown}
            size="sm"
          />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <span
                className="truncate text-[13px] font-medium text-zinc-100"
                title={selected.name}
              >
                {shown}
              </span>
              <span
                className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${kindPill}`}
              >
                {kindLabel}
              </span>
              {offline && (
                <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-rose-500/25 bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-medium text-rose-300">
                  <WifiOff size={10} /> offline
                </span>
              )}
              {!selected.delegable && (
                <span className="shrink-0 text-[10px] text-zinc-600">
                  (chat-only for now)
                </span>
              )}
              <span className="ml-auto shrink-0 text-[11px] tabular-nums text-zinc-500">
                {statsText(selected)}
              </span>
            </div>
            {selected.description && (
              <p className="mt-1 text-[11.5px] leading-relaxed text-zinc-500">
                {selected.description}
              </p>
            )}
          </div>
        </div>
      </Card>
    </Reveal>
  );
}
