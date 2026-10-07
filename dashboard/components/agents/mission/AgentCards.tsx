"use client";

// The RIGHT SIDEBAR (v1.307.0): one compact card per teammate — role, what it
// was handed, progress. Secondary on purpose: the deliverable owns the centre.
// A card expands in place for detail (latest action, its result, its files, a
// link to the full run) and collapses back; the default stays compact.
//
// THE BAR IS HONEST. A number is drawn only when the daemon sent one
// (`progress.pct` — done, waiting, or a plan's finished steps). A member
// working with no count behind it gets a moving stripe and its words
// ("Working · step 3"), never a percentage that only means "time passed".

import Link from "next/link";
import { useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import AgentFace, { type FaceMood } from "@/components/agents/AgentFace";
import {
  baseName,
  statusWord,
  type MemberStatus,
  type MissionMember,
  type MissionProgress,
} from "@/lib/mission";

function moodFor(s: MemberStatus): FaceMood {
  if (s === "working") return "work";
  if (s === "done") return "done";
  if (s === "failed") return "error";
  return "idle";
}

const STATUS_TONE: Record<MemberStatus, string> = {
  queued: "text-zinc-400",
  working: "text-accent",
  waiting_you: "text-amber-300",
  done: "text-emerald-300",
  failed: "text-rose-300",
  cancelled: "text-zinc-400",
};

export function ProgressBar({
  progress,
  status,
  testId,
}: {
  progress: MissionProgress;
  status: MemberStatus;
  testId?: string;
}) {
  const pct = progress.pct;
  const fill =
    status === "done"
      ? "bg-emerald-400/80"
      : status === "failed"
        ? "bg-rose-400/70"
        : status === "waiting_you"
          ? "bg-amber-300/80"
          : "bg-accent";
  return (
    <div className="flex items-center gap-2">
      <div
        data-testid={testId}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct ?? undefined}
        aria-valuetext={progress.label || undefined}
        className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-white/10"
      >
        {pct !== null ? (
          <div className={`h-full rounded-full ${fill} transition-all duration-500`} style={{ width: `${pct}%` }} />
        ) : status === "working" || status === "waiting_you" ? (
          <div
            data-indeterminate="true"
            className={`absolute inset-y-0 left-0 w-1/3 animate-pulse rounded-full ${fill} opacity-70`}
          />
        ) : null}
      </div>
      <span className="w-9 shrink-0 text-right text-[11px] tabular-nums text-zinc-400">
        {pct !== null ? `${pct}%` : ""}
      </span>
    </div>
  );
}

function AgentCard({ m, open, onToggle }: { m: MissionMember; open: boolean; onToggle: () => void }) {
  const key = m.session_id ?? m.agent;
  return (
    <li
      data-testid={`mission-card-${m.agent}`}
      data-status={m.status}
      className="rounded-xl border border-white/5 bg-white/[0.03] transition-colors hover:border-white/10"
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={`mission-card-detail-${key}`}
        onClick={onToggle}
        className="w-full px-3 py-2.5 text-left"
      >
        <div className="flex items-center gap-2.5">
          <AgentFace name={m.agent} mood={moodFor(m.status)} size={28} title="" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-[13px] font-semibold text-zinc-100">{m.name}</span>
              <span className={`shrink-0 text-[11px] ${STATUS_TONE[m.status]}`}>{statusWord(m.status)}</span>
            </div>
            <div className="truncate text-[12px] text-zinc-400" title={m.task}>
              {m.task || "—"}
            </div>
          </div>
          {open ? (
            <ChevronDown size={14} className="shrink-0 text-zinc-500" />
          ) : (
            <ChevronRight size={14} className="shrink-0 text-zinc-500" />
          )}
        </div>
        <div className="mt-2">
          <ProgressBar progress={m.progress} status={m.status} testId={`mission-bar-${m.agent}`} />
        </div>
      </button>
      {open && (
        <div
          id={`mission-card-detail-${key}`}
          data-testid={`mission-card-detail-${m.agent}`}
          className="space-y-2 border-t border-white/5 px-3 py-2.5 text-[12px] text-zinc-300"
        >
          {m.progress.label && <div className="text-zinc-400">{m.progress.label}</div>}
          {m.task && (
            <div>
              <div className="text-[11px] uppercase tracking-wide text-zinc-500">Handed</div>
              <div className="whitespace-pre-wrap">{m.task}</div>
            </div>
          )}
          {m.activity && (
            <div>
              <div className="text-[11px] uppercase tracking-wide text-zinc-500">Latest</div>
              <div>{m.activity}</div>
            </div>
          )}
          {m.waiting_on && (
            <div className="text-amber-200">
              Waiting for your OK to use {m.waiting_on.tool.replace(/_/g, " ")}.
            </div>
          )}
          {m.result && (
            <div>
              <div className="text-[11px] uppercase tracking-wide text-zinc-500">Reported</div>
              <div className="line-clamp-6 whitespace-pre-wrap text-zinc-300">{m.result}</div>
            </div>
          )}
          {m.files.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {m.files.slice(0, 6).map((f) => (
                <span key={f} title={f} className="rounded-md bg-white/5 px-1.5 py-0.5 text-[11px] text-zinc-300">
                  {baseName(f)}
                </span>
              ))}
            </div>
          )}
          {m.session_id && (
            <Link
              href={`/sessions/${encodeURIComponent(m.session_id)}`}
              className="inline-flex items-center gap-1 text-[12px] text-accent hover:underline"
            >
              Open the full run <ExternalLink size={11} />
            </Link>
          )}
        </div>
      )}
    </li>
  );
}

export function AgentCards({ members }: { members: MissionMember[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (members.length === 0) {
    return (
      <p data-testid="mission-cards-empty" className="px-1 text-[12px] text-zinc-500">
        Teammates appear here as Jarvis hands out the work.
      </p>
    );
  }
  return (
    <ul data-testid="mission-cards" className="space-y-2">
      {members.map((m) => {
        const key = m.session_id ?? m.agent;
        return (
          <AgentCard key={key} m={m} open={open === key} onToggle={() => setOpen(open === key ? null : key)} />
        );
      })}
    </ul>
  );
}
