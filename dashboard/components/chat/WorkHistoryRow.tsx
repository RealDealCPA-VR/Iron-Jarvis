"use client";

// Next leaps 13-b (build-out plan R2.10, idea 13 "Ask the record"): the rows
// under a reply that looked up earlier work ("what did we do for Baker in
// March?"). One row per piece of work, with how it stands and when; pressing
// the row opens that chat, run or coding session; Files and Decisions are
// quiet links beside it. Honest by construction (the SourcesRow rule): every
// row and every address comes from the `work_history` step's own result
// (lib/workHistory), never from the model's prose. Nothing is drawn when the
// step returned no rows.

import { useState } from "react";
import Link from "next/link";
import { History } from "lucide-react";
import {
  WORK_ROWS_LABEL,
  WORK_ROW_DECISIONS,
  WORK_ROW_FILES,
  dayWords,
  rowKindWords,
  type WorkHistoryRowData,
} from "@/lib/workHistory";

const QUIET =
  "shrink-0 rounded px-1 text-[12px] text-zinc-500 underline-offset-2 hover:text-accent-soft hover:underline focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent";

function statusClass(status: string): string {
  if (status === "failed" || status === "part failed") return "text-tone-danger";
  if (status === "needs you") return "text-tone-warn";
  return "text-zinc-400";
}

function Row({ row, onOpenFile }: { row: WorkHistoryRowData; onOpenFile?: (path: string) => void }) {
  const [filesOpen, setFilesOpen] = useState(false);
  const day = dayWords(row.when);
  const meta = [rowKindWords(row.kind), day, row.times > 1 ? `${row.times} times` : ""].filter(Boolean).join(" · ");
  const body = (
    <>
      <span className="min-w-0 truncate text-zinc-200">{row.title}</span>
      <span className="shrink-0 text-zinc-500">{meta}</span>
      {row.status && (
        <span className={`shrink-0 ${statusClass(row.status)}`} data-testid="work-history-status">
          {row.status}
        </span>
      )}
    </>
  );
  const canOpenFiles = Boolean(onOpenFile) && row.paths.length > 0;
  const several = row.paths.length > 1;
  return (
    <li data-testid="work-history-row" className="py-1">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[12px]">
        {row.link ? (
          <Link
            href={row.link}
            data-testid="work-history-open"
            className="flex min-w-0 max-w-full flex-1 items-baseline gap-x-2 rounded underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent"
          >
            {body}
          </Link>
        ) : (
          <span className="flex min-w-0 max-w-full flex-1 items-baseline gap-x-2">{body}</span>
        )}
        {(canOpenFiles || row.decisions) && (
          <span className="flex shrink-0 items-baseline gap-1">
            {canOpenFiles && (
              <button
                type="button"
                data-testid="work-history-files"
                aria-expanded={several ? filesOpen : undefined}
                className={QUIET}
                onClick={() => (several ? setFilesOpen((v) => !v) : onOpenFile?.(row.paths[0]))}
              >
                {WORK_ROW_FILES}
              </button>
            )}
            {row.decisions && (
              <Link href={row.decisions} data-testid="work-history-decisions" className={QUIET}>
                {WORK_ROW_DECISIONS}
              </Link>
            )}
          </span>
        )}
      </div>
      {several && filesOpen && canOpenFiles && (
        <ul className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 pl-2" data-testid="work-history-file-list">
          {row.paths.map((p) => (
            <li key={p} className="min-w-0 max-w-full">
              <button
                type="button"
                title={p}
                className="max-w-full truncate text-[12px] text-zinc-400 underline-offset-2 hover:text-accent-soft hover:underline focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent"
                onClick={() => onOpenFile?.(p)}
              >
                {p.split(/[\\/]/).pop() || p}
              </button>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function WorkHistoryRow({
  rows,
  onOpenFile,
}: {
  rows: WorkHistoryRowData[] | undefined;
  /** Opens a file in the panel beside the chat (in Quick Ask's window, the
   *  main window: the page's own `openDocument`). Absent: no Files press. */
  onOpenFile?: (path: string) => void;
}) {
  if (!rows || rows.length === 0) return null;
  return (
    <div className="mt-1.5 min-w-0 max-w-full" data-testid="work-history-rows">
      <span className="inline-flex items-center gap-1 text-[11px] text-zinc-500">
        <History size={10} className="shrink-0 text-accent-soft/70" aria-hidden /> {WORK_ROWS_LABEL}
      </span>
      <ul className="divide-y divide-white/5">
        {rows.map((r) => (
          <Row key={r.id} row={r} onOpenFile={onOpenFile} />
        ))}
      </ul>
    </div>
  );
}
