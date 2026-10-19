"use client";

/**
 * ONE CONDITIONAL LINE above the composer (calm UI redesign S8, AUDIT §4.5
 * R2, wireframe home.md).
 *
 * The home is a chat; nothing on it is permanent. When something needs the
 * user, ONE quiet line says so — the most urgent wins, the rest fold into it
 * ("+2 more") — and Open goes to Everything › Status, where the detail lives:
 *
 *   1. jobs a restart cut off
 *   2. a background task or a tool pack failing
 *   3. work running, or waiting for an answer
 *
 * v1.351.0 (readiness audit G02): a scheduled job whose last run failed or
 * finished short of the job (an ask expired, a step failed) gets its own
 * line — "1 scheduled job needs a look" — whose Open goes to /schedules,
 * where the row says what happened. It ranks under an ask that is waiting
 * NOW and above work that is merely running.
 *
 * One read for the counts (GET /ui/status-line) plus the pending-asks list
 * the bell already uses — never a session poll from the chat page.
 *
 * Daemon offline, a mock downgrade and "no model chosen" are the shell's own
 * banners (DaemonBanner, ProviderDowngradeBanner, SimulatedBanner), so they
 * are never repeated here. Nothing to say → nothing rendered.
 *
 * v1.355.0 (readiness audit G32): a failing background task also offers
 * "Ask Jarvis" beside Open. It opens Chat with HEALTH_QUESTION waiting in
 * the box (never sent); that question arms the health readers (app_health,
 * run_doctor, pack_status), so the answer reads the app's own health.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CalendarClock, CircleDot, RotateCcw } from "lucide-react";
import { usePolledApi } from "@/lib/useApi";
import { EVERYTHING_TAB_EVENT } from "@/lib/surfaces";
import { chatAskHrefFrom } from "@/lib/chatAsk";
import { HEALTH_QUESTION } from "@/lib/loopWords";

interface StatusCounts {
  interrupted?: number;
  failing_loops?: number;
  failing_packs?: number;
  running?: number;
  short_schedules?: number;
}
const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0);

export interface HomeLineItem {
  kind: "interrupted" | "failing" | "working" | "schedules";
  text: string;
}

/** Pure: the lines that apply, most urgent first. */
export function homeLineItems(input: {
  interrupted: number;
  failingLoops: number;
  failingPacks: number;
  running: number;
  waiting: number;
  /** Enabled schedules whose last run failed or fell short (v1.351.0). */
  shortSchedules?: number;
}): HomeLineItem[] {
  const out: HomeLineItem[] = [];
  const s = (n: number, one: string, many: string) => (n === 1 ? one : many);
  if (input.interrupted > 0) {
    out.push({
      kind: "interrupted",
      text: `${input.interrupted} ${s(input.interrupted, "job was", "jobs were")} interrupted by the last restart.`,
    });
  }
  const failing = input.failingLoops + input.failingPacks;
  if (failing > 0) {
    out.push({
      kind: "failing",
      text: `${failing} background ${s(failing, "task is", "tasks are")} failing.`,
    });
  }
  const short = input.shortSchedules ?? 0;
  const schedules: HomeLineItem | null =
    short > 0
      ? { kind: "schedules", text: `${short} scheduled ${s(short, "job needs", "jobs need")} a look` }
      : null;
  let working: HomeLineItem | null = null;
  if (input.running > 0 || input.waiting > 0) {
    const bits = [
      input.running > 0 ? `${input.running} ${s(input.running, "task", "tasks")} running` : "",
      input.waiting > 0 ? `${input.waiting} waiting for you` : "",
    ].filter(Boolean);
    working = { kind: "working", text: bits.join(" · ") };
  }
  // An ask waiting NOW outranks a schedule that fell short earlier; work that
  // is only running does not.
  const tail = input.waiting > 0 ? [working, schedules] : [schedules, working];
  for (const item of tail) if (item) out.push(item);
  return out;
}

const ICON = {
  interrupted: RotateCcw,
  failing: AlertTriangle,
  working: CircleDot,
  schedules: CalendarClock,
} as const;

export function HomeLine() {
  const counts = usePolledApi<StatusCounts>("/ui/status-line", 20000);
  const approvals = usePolledApi<{ approvals?: unknown[] }>("/chat/approvals/pending", 20000);
  // v1.355.0 (G32): the address this chat page is on, read after mount (the
  // line is drawn on the chat page itself, at "/" or "/chat"); Ask Jarvis
  // links to the OTHER one so a fresh chat mounts with the question waiting
  // (lib/chatAsk.chatAskHrefFrom says why).
  const [here, setHere] = useState("");
  useEffect(() => {
    setHere(window.location.pathname);
  }, []);
  const c = counts.data ?? {};
  const items = homeLineItems({
    interrupted: n(c.interrupted),
    failingLoops: n(c.failing_loops),
    failingPacks: n(c.failing_packs),
    running: n(c.running),
    waiting: Array.isArray(approvals.data?.approvals) ? approvals.data!.approvals!.length : 0,
    shortSchedules: n(c.short_schedules),
  });
  if (items.length === 0) return null;
  const [top, ...rest] = items;
  const Icon = ICON[top.kind];
  const tone = top.kind === "working" ? "text-zinc-300" : "text-tone-warn";
  return (
    <div
      data-testid="home-line"
      data-kind={top.kind}
      role="status"
      className="mb-2 flex items-center gap-2 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-1.5 text-xs"
    >
      <Icon size={13} className={`shrink-0 ${tone}`} aria-hidden />
      <span className={`min-w-0 flex-1 truncate ${tone}`}>
        {top.text}
        {rest.length > 0 && <span className="text-zinc-500"> +{rest.length} more</span>}
      </span>
      {top.kind === "failing" && (
        // v1.355.0 (G32): ask about it in chat, where the health readers run.
        <Link
          href={chatAskHrefFrom(here, HEALTH_QUESTION)}
          data-testid="home-line-ask"
          className="shrink-0 rounded-md px-2 py-0.5 text-[12px] text-zinc-300 hover:bg-white/[0.06] hover:text-zinc-100"
        >
          Ask Jarvis
        </Link>
      )}
      {top.kind === "schedules" ? (
        // The rows say what happened and link each run (v1.351.0, G02).
        <Link
          href="/schedules"
          className="shrink-0 rounded-md px-2 py-0.5 text-[12px] text-zinc-300 hover:bg-white/[0.06] hover:text-zinc-100"
        >
          Open
        </Link>
      ) : (
        <Link
          href="/everything#status"
          onClick={() => window.dispatchEvent(new CustomEvent(EVERYTHING_TAB_EVENT, { detail: "status" }))}
          className="shrink-0 rounded-md px-2 py-0.5 text-[12px] text-zinc-300 hover:bg-white/[0.06] hover:text-zinc-100"
        >
          Open
        </Link>
      )}
    </div>
  );
}
