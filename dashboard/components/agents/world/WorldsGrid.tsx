"use client";

/**
 * The Agents landing (v1.304.0): one card per project WORLD, plus "General".
 *
 * Asked for verbatim: "a round table for each set of agents … grouped by the
 * project; when a project is selected you enter the world of that project".
 * A card is the project's name, its team as LARGE stacked faces (40px — the
 * other half of the same request was "make the agent profile images more
 * prominent"), and the counts that make a world worth entering: what is
 * waiting on the user first, then what is running and what finished this
 * week. "General" is today's global rooms, untouched.
 */

import { Globe2, MessagesSquare, Users } from "lucide-react";
import AgentFace from "@/components/agents/AgentFace";
import { rosterAvatarSrc } from "@/components/agents/RosterStrip";
import {
  bareMemberName,
  countsLine,
  memberAvatar,
  memberKey,
  type WorldCard,
  type WorldMember,
} from "@/lib/agentWorlds";

/** A member's status, read from the roster fields the daemon sends with it. */
export type FaceStatus = "working" | "queued" | "paused" | "offline" | "ready" | "unknown";

export function faceStatus(m: WorldMember): FaceStatus {
  if (m.paused) return "paused";
  if (m.activity === "busy") return "working";
  if (m.activity === "queued") return "queued";
  if (m.healthy === false) return "offline";
  if (m.healthy === true || m.activity === "idle") return "ready";
  return "unknown";
}

const STATUS_DOT: Record<FaceStatus, string> = {
  working: "bg-accent",
  queued: "bg-sky-400",
  paused: "bg-zinc-500",
  offline: "bg-rose-500",
  ready: "bg-emerald-400",
  unknown: "bg-zinc-600",
};

const STATUS_WORD: Record<FaceStatus, string> = {
  working: "working",
  queued: "has work queued",
  paused: "paused",
  offline: "offline",
  ready: "ready",
  unknown: "",
};

/**
 * A team as a ROW of faces (v1.304.0 polish). NO OVERLAP — the first cut
 * reused the thread rail's overlapping stack, and at 40px each face was half
 * hidden behind the next, which is the opposite of "make the agent profile
 * images more prominent". Each face keeps its own space (a gap, never a
 * negative margin), carries its name as a tooltip and a small status dot;
 * past `max` the rest are a "+N" chip.
 */
export function TeamFaces({
  team,
  size = 40,
  max = 6,
}: {
  team: WorldMember[];
  size?: number;
  max?: number;
}) {
  const unique: WorldMember[] = [];
  const seen = new Set<string>();
  for (const m of team) {
    const key = memberKey(m);
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(m);
  }
  const shown = unique.slice(0, max);
  const extra = unique.length - shown.length;
  const dot = Math.max(8, Math.round(size / 4));
  return (
    <ul data-testid="team-faces" className="flex flex-wrap items-center gap-2" aria-label="Team">
      {shown.map((m) => {
        const name = bareMemberName(m.name);
        const status = faceStatus(m);
        const rel = memberAvatar(m);
        const tip = STATUS_WORD[status] ? `${name} — ${STATUS_WORD[status]}` : name;
        return (
          <li
            key={memberKey(m)}
            data-testid={`team-face-${m.name}`}
            data-status={status}
            title={tip}
            className="relative shrink-0"
            style={{ width: size, height: size }}
          >
            <AgentFace
              name={name}
              size={size}
              avatarUrl={rel ? rosterAvatarSrc(rel, m.last_active) : undefined}
              title=""
            />
            <span
              aria-hidden
              className={`absolute bottom-0 right-0 rounded-full ring-2 ring-ink-900 ${STATUS_DOT[status]}`}
              style={{ width: dot, height: dot }}
            />
            <span className="sr-only">{tip}</span>
          </li>
        );
      })}
      {extra > 0 && (
        <li
          data-testid="team-faces-more"
          className="grid shrink-0 place-items-center rounded-full border hairline bg-white/[0.04] text-xs font-semibold text-zinc-300"
          style={{ width: size, height: size }}
          title={unique
            .slice(max)
            .map((m) => bareMemberName(m.name))
            .join(", ")}
        >
          +{extra}
        </li>
      )}
    </ul>
  );
}

export function WorldsGrid({
  worlds,
  generalCount,
  onEnter,
  onGeneral,
}: {
  worlds: WorldCard[];
  /** GET /agents/worlds → general.thread_count (absent on a partial answer). */
  generalCount?: number;
  onEnter: (projectId: string) => void;
  onGeneral: () => void;
}) {
  return (
    <section data-testid="worlds-grid" aria-label="Agent worlds" className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-zinc-100">Agents</h1>
        <p className="text-sm text-zinc-500">
          Each project is a world with its own team and round table. Pick one to step in.
        </p>
      </div>
      {/* 1 column narrow, 2 medium, 3 wide (pinned). */}
      <ul data-testid="worlds-grid-list" className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {worlds.map((w) => {
          const waiting = w.counts.waiting;
          return (
            <li key={w.project.id}>
              <button
                type="button"
                data-testid={`world-card-${w.project.id}`}
                onClick={() => onEnter(w.project.id)}
                className="card-surface group flex h-full min-h-[11rem] w-full flex-col gap-4 p-5 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 motion-reduce:transition-none motion-reduce:hover:translate-y-0"
                style={
                  w.project.color
                    ? { borderTopColor: w.project.color, borderTopWidth: 3 }
                    : undefined
                }
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-2">
                    <Globe2 size={15} className="shrink-0 text-accent-soft" aria-hidden />
                    <span className="truncate text-[15px] font-semibold text-zinc-100">
                      {w.project.name || "Untitled project"}
                    </span>
                  </span>
                  {waiting > 0 && (
                    <span
                      data-testid={`world-waiting-${w.project.id}`}
                      className="flex shrink-0 items-center gap-1.5 rounded-full border border-amber-400/50 bg-amber-400/20 px-2.5 py-1 text-xs font-semibold text-amber-300 shadow-[0_0_12px_rgba(251,191,36,0.25)]"
                      title={`${waiting} waiting on you`}
                    >
                      <span className="text-sm font-bold leading-none">{waiting}</span> waiting on you
                    </span>
                  )}
                </div>
                <div className="flex min-h-[40px] flex-1 items-center">
                  {w.team.length > 0 ? (
                    <TeamFaces team={w.team} size={40} max={6} />
                  ) : (
                    <span className="flex items-center gap-1.5 text-xs text-zinc-500">
                      <Users size={14} aria-hidden /> No team yet — step in to build one
                    </span>
                  )}
                </div>
                <p
                  data-testid={`world-counts-${w.project.id}`}
                  className="text-xs text-zinc-400"
                >
                  {countsLine(w.counts)}
                </p>
              </button>
            </li>
          );
        })}
        <li>
          <button
            type="button"
            data-testid="world-card-general"
            onClick={onGeneral}
            className="card-surface flex h-full min-h-[11rem] w-full flex-col gap-4 p-5 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 motion-reduce:transition-none motion-reduce:hover:translate-y-0"
          >
            <span className="flex items-center gap-2">
              <MessagesSquare size={15} className="text-accent-soft" aria-hidden />
              <span className="text-[15px] font-semibold text-zinc-100">General</span>
            </span>
            <p className="text-sm text-zinc-400">
              Rooms that belong to no project — talk with any agent, built-in, yours, or on
              another computer.
            </p>
            {typeof generalCount === "number" && (
              <p data-testid="world-general-count" className="text-xs text-zinc-500">
                {generalCount === 1 ? "1 room" : `${generalCount} rooms`}
              </p>
            )}
          </button>
        </li>
      </ul>
    </section>
  );
}

export default WorldsGrid;
