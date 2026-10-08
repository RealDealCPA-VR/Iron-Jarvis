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
 * One read for the counts (GET /ui/status-line) plus the pending-asks list
 * the bell already uses — never a session poll from the chat page.
 *
 * Daemon offline, a mock downgrade and "no model chosen" are the shell's own
 * banners (DaemonBanner, ProviderDowngradeBanner, SimulatedBanner), so they
 * are never repeated here. Nothing to say → nothing rendered.
 */

import Link from "next/link";
import { AlertTriangle, CircleDot, RotateCcw } from "lucide-react";
import { usePolledApi } from "@/lib/useApi";
import { EVERYTHING_TAB_EVENT } from "@/lib/surfaces";

interface StatusCounts {
  interrupted?: number;
  failing_loops?: number;
  failing_packs?: number;
  running?: number;
}
const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0);

export interface HomeLineItem {
  kind: "interrupted" | "failing" | "working";
  text: string;
}

/** Pure: the lines that apply, most urgent first. */
export function homeLineItems(input: {
  interrupted: number;
  failingLoops: number;
  failingPacks: number;
  running: number;
  waiting: number;
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
  if (input.running > 0 || input.waiting > 0) {
    const bits = [
      input.running > 0 ? `${input.running} ${s(input.running, "task", "tasks")} running` : "",
      input.waiting > 0 ? `${input.waiting} waiting for you` : "",
    ].filter(Boolean);
    out.push({ kind: "working", text: bits.join(" · ") });
  }
  return out;
}

const ICON = { interrupted: RotateCcw, failing: AlertTriangle, working: CircleDot } as const;

export function HomeLine() {
  const counts = usePolledApi<StatusCounts>("/ui/status-line", 20000);
  const approvals = usePolledApi<{ approvals?: unknown[] }>("/chat/approvals/pending", 20000);
  const c = counts.data ?? {};
  const items = homeLineItems({
    interrupted: n(c.interrupted),
    failingLoops: n(c.failing_loops),
    failingPacks: n(c.failing_packs),
    running: n(c.running),
    waiting: Array.isArray(approvals.data?.approvals) ? approvals.data!.approvals!.length : 0,
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
      <Link
        href="/everything#status"
        onClick={() => window.dispatchEvent(new CustomEvent(EVERYTHING_TAB_EVENT, { detail: "status" }))}
        className="shrink-0 rounded-md px-2 py-0.5 text-[12px] text-zinc-300 hover:bg-white/[0.06] hover:text-zinc-100"
      >
        Open
      </Link>
    </div>
  );
}
