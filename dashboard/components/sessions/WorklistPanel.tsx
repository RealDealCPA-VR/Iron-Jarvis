"use client";

// Worklist panel (v1.174.0, P3) — "N of M done", derived from the record.
//
// THE FAILURE THIS ANSWERS. A 26-file bulk job reported "FAILED — reached max
// steps before completion" and nothing else. The user could not tell whether it
// had done 0 files or 24: the only account of the work was the run's prose, and
// the prose was gone. The daemon now keeps a durable per-item worklist
// (`GET /worklist/{sessionId}`, scoped to the department's ROOT session), so
// this panel can state progress as a COUNT OF ROWS — never a number an agent
// said out loud.
//
// Three deliberate choices:
//
//  * it renders NOTHING when the board is empty. Most sessions are not bulk
//    jobs, and an always-on empty "Worklist" card would advertise machinery
//    that is not in play (the BlackboardPanel rule).
//  * FAILED items come first and carry their notes. "3 failed" with no reason
//    is the same missing information the whole wave exists to fix — and on the
//    acceptance folder the reasons are the story ("image-only scan").
//  * the item list is CLIPPED by the server at MAX_VIEW_ITEMS and this says so.
//    A capped list that reads as complete is the silent-truncation lie; the
//    counts stay authoritative because they are computed from every row.
//
// Polls ~5s while the session is active so a long job's progress is watchable.

import { useEffect, useState } from "react";
import { ListChecks } from "lucide-react";
import { get } from "@/lib/api";
import { Card, Badge } from "@/components/ui";

export interface WorklistItemView {
  id: string;
  key: string;
  label: string;
  status: string; // pending | doing | done | failed
  note: string;
  claimed_by: string;
  result_key: string;
  updated_at: string;
}

export interface WorklistSummary {
  board_id: string;
  total: number;
  done: number;
  failed: number;
  pending: number;
  doing: number;
  remaining: number;
  complete: boolean;
}

export interface WorklistResponse {
  board_id: string;
  summary: WorklistSummary;
  items: WorklistItemView[];
  clipped: boolean;
}

/** How many keys of one group to show before "+N more". */
const SHOWN = 6;

export function WorklistPanel({
  sessionId,
  active,
}: {
  sessionId: string;
  active: boolean;
}) {
  const [board, setBoard] = useState<WorklistResponse | null>(null);
  const [shownFor, setShownFor] = useState(sessionId);

  if (shownFor !== sessionId) {
    // Drop the previous session's board AS THE PROP CHANGES (React's
    // "adjusting state when a prop changes" — the reset must not wait for an
    // effect, or A's counts paint under B's heading for a frame). The App
    // Router reuses this component across /sessions/A -> /sessions/B, so
    // without this B renders A's "12 of 26 done" until B's fetch lands — and
    // if B's fetch fails, forever. "Absent beats wrong", and stale counts
    // under the wrong session are wrong, not absent. Keyed on sessionId ALONE:
    // clearing when `active` flips would blank a finished job's panel for no
    // reason.
    setShownFor(sessionId);
    setBoard(null);
  }

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await get<WorklistResponse>(`/worklist/${sessionId}`);
        if (alive) setBoard(res ?? null);
      } catch {
        // Optional panel. A failed refetch CLEARS rather than freezing the last
        // good board: a panel that keeps showing counts the daemon can no
        // longer confirm is the silent-staleness lie in miniature.
        if (alive) setBoard(null);
      }
    };
    void load();
    if (!active) {
      return () => {
        alive = false;
      };
    }
    const timer = setInterval(() => void load(), 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [sessionId, active]);

  const summary = board?.summary;
  const total = summary?.total ?? 0;
  if (!summary || total === 0) return null;

  const items = board?.items ?? [];
  const failed = items.filter((i) => i.status === "failed");
  const outstanding = items.filter(
    (i) => i.status === "pending" || i.status === "doing",
  );
  // Percent from the COUNTS, not from the (clipped) rows — the two disagree the
  // moment a board is larger than one read.
  const donePct = Math.round((summary.done / Math.max(1, total)) * 100);

  return (
    <Card
      title={`Worklist · ${summary.done} of ${total} done`}
      icon={<ListChecks size={15} />}
      right={
        <span className="flex items-center gap-1.5" data-testid="worklist-badges">
          {summary.doing > 0 && (
            <Badge value={`${summary.doing} in progress`} tone="cyan" />
          )}
          {summary.pending > 0 && (
            <Badge value={`${summary.pending} pending`} tone="slate" />
          )}
          {summary.failed > 0 && (
            <Badge value={`${summary.failed} failed`} tone="red" />
          )}
          {summary.complete && <Badge value="complete" tone="green" />}
        </span>
      }
    >
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]"
        role="progressbar"
        aria-valuenow={donePct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Worklist progress"
      >
        <div
          className="h-full rounded-full bg-accent/70 transition-[width] duration-500"
          style={{ width: `${donePct}%` }}
        />
      </div>

      {failed.length > 0 && (
        <Group
          testId="worklist-failed"
          title={`Failed · ${summary.failed}`}
          tone="text-rose-300"
          rows={failed}
          total={summary.failed}
          withNote
        />
      )}
      {outstanding.length > 0 && (
        <Group
          testId="worklist-pending"
          title={`Still to do · ${summary.remaining}`}
          tone="text-zinc-300"
          rows={outstanding}
          total={summary.remaining}
        />
      )}
      {board?.clipped && (
        <div className="mt-2 text-[11px] text-zinc-500">
          Showing {items.length} of {total} items — the counts above cover all of
          them.
        </div>
      )}
    </Card>
  );
}

function Group({
  title,
  tone,
  rows,
  total,
  withNote = false,
  testId,
}: {
  title: string;
  tone: string;
  rows: WorklistItemView[];
  total: number;
  withNote?: boolean;
  testId: string;
}) {
  const shown = rows.slice(0, SHOWN);
  // "+N more" counts against the SUMMARY total, not the rows we happen to hold:
  // with a clipped response the row list is short and the honest remainder is
  // still the number of items in that state.
  const hidden = Math.max(0, total - shown.length);
  return (
    <div className="mt-3" data-testid={testId}>
      <div
        className={`text-[11px] font-medium uppercase tracking-[0.1em] ${tone}`}
      >
        {title}
      </div>
      <div className="mt-1.5 space-y-1">
        {shown.map((row) => (
          <div
            key={row.id}
            className="rounded-lg border border-white/[0.04] bg-white/[0.015] px-2.5 py-1.5"
          >
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-300">
                {row.key}
              </span>
              {row.status === "doing" && (
                <span className="shrink-0 text-[11px] text-accent-soft/80">
                  in progress
                </span>
              )}
            </div>
            {withNote && row.note && (
              <div className="mt-0.5 text-[11px] text-zinc-400">{row.note}</div>
            )}
          </div>
        ))}
        {hidden > 0 && (
          <div className="px-2.5 text-[11px] text-zinc-500">
            … and {hidden} more
          </div>
        )}
      </div>
    </div>
  );
}

export default WorklistPanel;
