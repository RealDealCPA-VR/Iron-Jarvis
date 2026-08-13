"use client";

// v1.169.0 P1 — the project HEARTBEAT: which task schedules run on the user's
// behalf INSIDE this project, when the next fire is, and how the last one
// went. Same outcome-truth idiom as the Schedules page (v1.119.0: status +
// detail + a deep link to the actual session), filtered to one project —
// v1.165.0's truth-where-the-user-stands applied to automation. Renders
// NOTHING when the project has no task schedules: an empty card on every
// project would be noise, not a heartbeat.

import Link from "next/link";
import { ArrowUpRight, CalendarClock, Clock } from "lucide-react";
import { useApi } from "@/lib/useApi";
import { cronLabel } from "@/lib/schedules";
import type { Schedule } from "@/lib/types";
import { Card, Dot } from "@/components/ui";

/** The project a schedule runs inside. Prefers the server-decoded
 * `project_id` (additive on GET /schedules since v1.169.0); falls back to
 * parsing the payload blob the Schedules page already ships and parses
 * client-side, so a row from an older daemon still lands in the right place. */
export function scheduleProjectId(s: Schedule): string {
  if (typeof s.project_id === "string" && s.project_id) return s.project_id;
  try {
    const p = JSON.parse(s.payload_json || "{}") as Record<string, unknown>;
    return typeof p.project_id === "string" ? p.project_id : "";
  } catch {
    return ""; // unparseable payload — no project claim, not a crash
  }
}

/** Task-kind schedules bound to this project — the rows the surface shows.
 * Kind matters: a workflow/event schedule carrying a project_id is not "an
 * agent working in this project on a clock". */
export function projectSchedules(all: Schedule[], projectId: string): Schedule[] {
  if (!projectId) return [];
  return all.filter((s) => s.kind === "task" && scheduleProjectId(s) === projectId);
}

/** The schedule's task text — what fires, in the user's own words. */
export function scheduleTask(s: Schedule): string {
  try {
    const p = JSON.parse(s.payload_json || "{}") as Record<string, unknown>;
    return typeof p.task === "string" ? p.task : "";
  } catch {
    return "";
  }
}


/** Humanized trigger, the Schedules page's triggerLabel approach: one-time
 * date → "Once · <local time>", interval → "Every Ns", preset cron → its
 * friendly label ("Weekly Fri 4pm"). An unknown cron keeps the expression
 * ("Custom cron · 0 3 * * 2") — unlike the page, this label is the ONLY
 * place the trigger shows (the next-run tooltip), so dropping the raw
 * expression would lose the one thing a power user needs to verify. */
export function scheduleRepeatLabel(s: Schedule): string {
  const tt = (s.trigger_type ?? "").toLowerCase();
  if (tt === "date" || (!s.cron && s.run_at)) {
    return s.run_at ? `Once · ${new Date(s.run_at).toLocaleString()}` : "Once";
  }
  if (tt === "interval" || (!s.cron && s.interval_seconds)) {
    return s.interval_seconds ? `Every ${s.interval_seconds}s` : "Interval";
  }
  if (s.cron) return cronLabel(s.cron) ?? `Custom cron · ${s.cron}`;
  return "—";
}

/** This project's schedules row — "what runs here, and did last night's run
 * succeed?". Rendered by the Tasks surface under the run-a-task card. */
export function ProjectSchedules({ projectId }: { projectId: string }) {
  const { data, error } = useApi<{ schedules: Schedule[] }>("/schedules");
  const mine = projectSchedules(data?.schedules ?? [], projectId);
  // Honesty over tidiness: when the list could not load, this project's
  // schedules are UNKNOWN, not absent — vanishing here would be
  // indistinguishable from "nothing runs in this project", the exact collapse
  // v1.165.0 exists to prevent. Same hint idiom as SurfaceMedia/SurfaceBoard
  // (status 0 = daemon unreachable; anything else = the daemon errored).
  if (error) {
    return (
      <Card title="Schedules" icon={<CalendarClock size={15} />}>
        <p className="py-2 text-sm text-zinc-500">
          {error.status === 0
            ? "Schedules unavailable — the daemon looks offline."
            : `Schedules unavailable — the daemon returned an error (HTTP ${error.status}).`}
        </p>
      </Card>
    );
  }
  // Absent beats an empty box, but ONLY for the genuine cases: still loading,
  // or the list loaded and nothing here is scheduled.
  if (mine.length === 0) return null;
  return (
    <Card
      title={`Schedules · ${mine.length}`}
      icon={<CalendarClock size={15} />}
      right={
        <Link
          href="/schedules"
          className="text-[11px] text-accent-soft transition-colors hover:text-accent"
        >
          manage →
        </Link>
      }
    >
      <ul className="space-y-0.5" data-testid="project-schedules">
        {mine.map((s) => (
          <li
            key={s.name}
            className="flex items-center gap-2.5 rounded-md px-1.5 py-1.5 transition-colors hover:bg-white/[0.02]"
          >
            <Dot on={!!s.enabled} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs text-zinc-200">{s.name}</span>
              {scheduleTask(s) && (
                <span
                  className="block truncate text-[11px] text-zinc-500"
                  title={scheduleTask(s)}
                >
                  {scheduleTask(s)}
                </span>
              )}
            </span>
            <span className="flex shrink-0 flex-col items-end">
              {/* Outcome truth (v1.119.0): how the last fire went + the session. */}
              {s.last_status === "ok" ? (
                <span className="text-[11px] text-emerald-300">✓ ok</span>
              ) : s.last_status === "error" ? (
                <span
                  className="max-w-[200px] truncate text-[11px] text-rose-300"
                  title={s.last_detail || undefined}
                >
                  ✗ failed{s.last_detail ? ` — ${s.last_detail}` : ""}
                </span>
              ) : (
                <span className="text-[11px] text-zinc-600">not run yet</span>
              )}
              {s.last_session_id ? (
                <Link
                  href={`/sessions/${encodeURIComponent(s.last_session_id)}`}
                  className="inline-flex items-center gap-1 text-[11px] text-accent-soft transition-colors hover:text-accent"
                >
                  open session <ArrowUpRight size={11} />
                </Link>
              ) : null}
            </span>
            <span
              className="inline-flex w-44 shrink-0 items-center justify-end gap-1.5 text-right text-[11px] text-zinc-500"
              title={scheduleRepeatLabel(s)}
            >
              <Clock size={12} className="shrink-0 text-zinc-600" />
              {s.next_run ? new Date(s.next_run).toLocaleString() : "—"}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
