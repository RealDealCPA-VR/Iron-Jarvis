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
//
// v1.309.0: an expanded card names the model that teammate ran on, and a
// teammate still at work can be STOPPED on its own (`POST /sessions/<its
// id>/cancel` — the daemon ends that run between steps and the coordinator
// is told the user stopped it; the rest of the team carries on). The list is
// memoised: the screen re-renders on a real change only, never per token.

import Link from "next/link";
import { memo, useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink, Square } from "lucide-react";
import { post } from "@/lib/api";
import AgentFace, { type FaceMood } from "@/components/agents/AgentFace";
import {
  baseName,
  statusWord,
  type MemberStatus,
  type MissionMember,
  type MissionProgress,
} from "@/lib/mission";
import { receiptVerbFor } from "./receipt";

function moodFor(s: MemberStatus): FaceMood {
  if (s === "working") return "work";
  if (s === "done") return "done";
  if (s === "failed") return "error";
  return "idle";
}

// v1.329.0 (calm chat wave 6): theme tokens only, never a literal hue, so a
// card reads the same way on every theme (Daylight included) as the chat's
// receipt does.
const STATUS_TONE: Record<MemberStatus, string> = {
  queued: "text-zinc-400",
  working: "text-accent",
  waiting_you: "text-tone-warn",
  done: "text-tone-success",
  failed: "text-tone-danger",
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
      ? "bg-tone-success/80"
      : status === "failed"
        ? "bg-tone-danger/70"
        : status === "waiting_you"
          ? "bg-tone-warn/80"
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

/** The mock's line (v1.329.0), in the chat receipt's words: "Mock answer. No
 *  real model ran." (TurnReceipt) for an answer, the same second sentence for
 *  a run that has not answered. Plain sentences, no dash aside. The mission
 *  receipt (MissionOutput) and every teammate card read it from here; a card
 *  names what it ran on in parentheses before the last stop (MockRanOn). */
export function mockLine(answered: boolean): string {
  return answered ? "Mock answer. No real model ran." : "On the mock. No real model ran.";
}

/** The card's mock line with what it ran on: "Mock answer. No real model ran
 *  (mock · mock-1)." The parentheses never break inside (a narrow card used
 *  to wrap "mock-" from "1"). */
function MockRanOn({ answered, ranOn }: { answered: boolean; ranOn: string }) {
  return (
    <>
      {mockLine(answered).replace(/\.$/, "")} <span className="whitespace-nowrap">({ranOn})</span>.
    </>
  );
}

/** A teammate that is still running (or parked on an ask) can be stopped. */
const STOPPABLE: ReadonlySet<MemberStatus> = new Set(["queued", "working", "waiting_you"]);

function StopTeammate({ m, onChanged }: { m: MissionMember; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (!m.session_id || !STOPPABLE.has(m.status)) return null;
  const sid = m.session_id;
  const stop = async () => {
    setBusy(true);
    setNote(null);
    try {
      await post(`/sessions/${encodeURIComponent(sid)}/cancel`, {});
      setNote("Stopping. Jarvis carries on with the rest of the team.");
      onChanged();
    } catch (e) {
      setNote(e instanceof Error && e.message ? e.message : "Could not stop it.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <button
        type="button"
        data-testid={`mission-card-stop-${m.agent}`}
        disabled={busy}
        onClick={() => void stop()}
        className="btn-ghost px-2.5 py-1 text-[12px]"
      >
        <Square size={11} /> Stop {m.name}
      </button>
      {note && <div className="mt-1 text-[12px] text-zinc-400">{note}</div>}
    </div>
  );
}

function AgentCard({
  m,
  open,
  onToggle,
  onChanged,
}: {
  m: MissionMember;
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const key = m.session_id ?? m.agent;
  const ranOn = [m.provider, m.model].filter((x, i, all) => x && all.indexOf(x) === i).join(" · ");
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
          {ranOn && (
            <div
              data-testid={`mission-card-model-${m.agent}`}
              className={`text-[12px] ${m.provider === "mock" ? "text-tone-warn" : "text-zinc-500"}`}
            >
              {/* v1.310.0: the coordinator receipt's tense, by THIS
                  teammate's status (./receipt) — a failed teammate "tried"
                  its model, it was never "answered by" it.
                  v1.329.0: the mock line says it in the chat receipt's own
                  words ("Mock answer. No real model ran.") and its warn
                  tone, with what it ran on in parentheses. */}
              {m.provider === "mock" ? (
                <MockRanOn answered={m.status === "done"} ranOn={ranOn} />
              ) : (
                `${receiptVerbFor(m.status)} ${ranOn}`
              )}
            </div>
          )}
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
            <div className="text-tone-warn">
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
          <StopTeammate m={m} onChanged={onChanged} />
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

export const AgentCards = memo(function AgentCards({
  members,
  onChanged = noop,
}: {
  members: MissionMember[];
  /** Something on a card changed the mission (a teammate stopped) — reload. */
  onChanged?: () => void;
}) {
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
      {members.map((m, i) => {
        // A remote teammate has no session; one asked twice is two rows with
        // the same name, so its key carries its position (v1.309.0 review).
        const key = m.session_id ?? `${m.agent}#${i}`;
        return (
          <AgentCard
            key={key}
            m={m}
            open={open === key}
            onToggle={() => setOpen(open === key ? null : key)}
            onChanged={onChanged}
          />
        );
      })}
    </ul>
  );
});

function noop() {}
