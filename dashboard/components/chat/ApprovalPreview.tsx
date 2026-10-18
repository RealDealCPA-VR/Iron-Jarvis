"use client";

// See it before you allow it (v1.348.0, polish wave A). The quiet block inside
// ApprovalCard that shows what a call WILL DO: a file edit as a compact diff,
// a file write as its first lines, a command as one terminal line, a plan to
// approve (v1.350.0, `approve_plan`) as a numbered list of its steps, and every
// other argument as a tidy `key: value` line. lib/approvalPreview.ts decides
// what each argument is; this file only draws it.
//
// CALM, ON PURPOSE: no box, no border, no shadow. Monospace and the shared
// diff tints (bg-tone-success/10, bg-tone-danger/10, the same tokens the
// changed-files view uses) are the only marks, so the card the user already
// knows keeps its look. Long parts fold behind ONE quiet text button ("Show
// all N lines" / "Show in full"), which is a real <button> with aria-expanded:
// the dock's Enter never fires from a button (lib/dockAsk), so pressing Enter
// on it opens the lines and never answers the card.

import { useId, useMemo, useState } from "react";
import {
  approvalPreview,
  countedRows,
  PREVIEW_LINES,
  type ArgEntry,
  type PreviewPart,
  type PreviewRow,
} from "@/lib/approvalPreview";

const TOGGLE =
  "rounded text-[11px] text-zinc-500 underline-offset-2 transition-colors hover:text-zinc-300 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 motion-reduce:transition-none";

/** The quiet "Show all N lines" / "Show fewer lines" control under a block. */
function MoreToggle({
  open,
  onToggle,
  controls,
  closedLabel,
  openLabel,
  testId,
}: {
  open: boolean;
  onToggle: () => void;
  controls: string;
  closedLabel: string;
  openLabel: string;
  testId: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
      className={TOGGLE}
    >
      {open ? openLabel : closedLabel}
    </button>
  );
}

/** One argument on one line (wraps, never truncated by CSS). */
function ArgLine({ entry, full }: { entry: ArgEntry; full: boolean }) {
  const label = <span className="text-zinc-500">{entry.key}:</span>;
  if (entry.empty) {
    return (
      <p className="break-all text-[11px] text-zinc-400">
        {label} <span className="text-zinc-500">(empty)</span>
      </p>
    );
  }
  if (full && entry.cut && entry.nested) {
    return (
      <div className="text-[11px] text-zinc-400">
        {label}
        <pre className="mt-0.5 whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-zinc-300">
          {entry.full}
        </pre>
      </div>
    );
  }
  // v1.314.0: wrap, never truncate. A cut here is the model's long text with
  // an ellipsis and a "Show in full" beside it, never a path (those are kept).
  return (
    <p className="whitespace-pre-wrap break-all text-[11px] text-zinc-400">
      {label} {full ? entry.full : entry.short}
    </p>
  );
}

const ROW_TINT: Record<PreviewRow["kind"], string> = {
  added: "bg-tone-success/10",
  removed: "bg-tone-danger/10",
  same: "",
  gap: "",
  note: "",
};

function DiffRowLine({ row }: { row: PreviewRow }) {
  if (row.kind === "gap") {
    return (
      <div aria-hidden="true" data-kind="gap" className="select-none pl-4 text-zinc-600">
        ⋯
      </div>
    );
  }
  if (row.kind === "note") {
    return (
      <div data-kind="note" className="flex">
        <span aria-hidden="true" className="w-4 shrink-0 select-none" />
        <span className="min-w-0 whitespace-pre-wrap break-all font-sans text-[11px] text-zinc-400">
          {row.text}
        </span>
      </div>
    );
  }
  const mark = row.kind === "added" ? "+" : row.kind === "removed" ? "−" : " ";
  const markTone =
    row.kind === "added" ? "text-tone-success" : row.kind === "removed" ? "text-tone-danger" : "";
  return (
    <div data-kind={row.kind} className={`flex ${ROW_TINT[row.kind]}`}>
      <span aria-hidden="true" className={`w-4 shrink-0 select-none text-center ${markTone}`}>
        {mark}
      </span>
      {row.kind !== "same" && (
        <span className="sr-only">{row.kind === "added" ? "added: " : "removed: "}</span>
      )}
      <span className="min-w-0 whitespace-pre-wrap break-all pr-2 text-zinc-300">
        {row.text || " "}
      </span>
    </div>
  );
}

/** A file edit: the +/− counts, then the change, capped with "Show all". */
function DiffBlock({ part }: { part: Extract<PreviewPart, { type: "diff" }> }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const total = countedRows(part.rows);
  // Cap by drawn rows; a separator counts toward the cap so the block's
  // height is what the cap says.
  const capped = part.rows.length > PREVIEW_LINES;
  const shown = open || !capped ? part.rows : part.rows.slice(0, PREVIEW_LINES);
  return (
    <div data-testid="approval-diff" className="space-y-1">
      <p className="text-[11px] text-zinc-400">
        <span className="text-zinc-500">change:</span>{" "}
        <span data-testid="approval-diff-counts" className="tabular-nums">
          <span className="text-tone-success">+{part.added}</span>{" "}
          <span className="text-tone-danger">−{part.removed}</span>
        </span>
      </p>
      {part.rows.length === 0 ? (
        <p className="text-[11px] text-zinc-500">No lines change.</p>
      ) : (
        <div
          id={id}
          role="group"
          aria-label={`The change: ${part.added} lines added, ${part.removed} removed`}
          className="font-mono text-[12px] leading-relaxed"
        >
          {shown.map((r, i) => (
            <DiffRowLine key={i} row={r} />
          ))}
        </div>
      )}
      {capped && (
        <MoreToggle
          open={open}
          onToggle={() => setOpen((v) => !v)}
          controls={id}
          closedLabel={`Show all ${total} lines`}
          openLabel="Show fewer lines"
          testId="approval-diff-more"
        />
      )}
    </div>
  );
}

/** A file write: the new text's first lines, capped with "Show all". */
function ContentBlock({ lines }: { lines: string[] }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const capped = lines.length > PREVIEW_LINES;
  const shown = open || !capped ? lines : lines.slice(0, PREVIEW_LINES);
  return (
    <div data-testid="approval-content" className="space-y-1">
      <p className="text-[11px] text-zinc-400">
        <span className="text-zinc-500">content:</span>{" "}
        {lines.length === 0 ? (
          <span className="text-zinc-500">(empty)</span>
        ) : lines.length === 1 ? (
          "1 line"
        ) : (
          `${lines.length} lines`
        )}
      </p>
      {lines.length > 0 && (
        <pre
          id={id}
          className="whitespace-pre-wrap break-all font-mono text-[12px] leading-relaxed text-zinc-300"
        >
          {shown.join("\n")}
        </pre>
      )}
      {capped && (
        <MoreToggle
          open={open}
          onToggle={() => setOpen((v) => !v)}
          controls={id}
          closedLabel={`Show all ${lines.length} lines`}
          openLabel="Show fewer lines"
          testId="approval-content-more"
        />
      )}
    </div>
  );
}

/** What wants to run, verbatim, as one terminal line. The `$` is drawn for
 *  the eye only; a screen reader hears the command itself. */
function CommandLine({ command }: { command: string }) {
  return (
    <pre
      data-testid="approval-command"
      className="max-h-60 overflow-y-auto whitespace-pre-wrap break-all font-mono text-[12px] leading-relaxed text-zinc-200"
    >
      <span aria-hidden="true" className="select-none text-zinc-500">
        ${" "}
      </span>
      {command}
    </pre>
  );
}

/** v1.350.0 (lane C1): the plan the agent asks to follow (the optional plan
 *  ask, tool `approve_plan`), as a numbered list in the card's own quiet
 *  type. Each goal is whole (it wraps, never cut); a long plan shows its
 *  first lines with the same "Show all" control a long diff has. */
function PlanSteps({ part }: { part: Extract<PreviewPart, { type: "plan" }> }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const n = part.steps.length + part.more;
  const capped = part.steps.length > PREVIEW_LINES;
  const shown = open || !capped ? part.steps : part.steps.slice(0, PREVIEW_LINES);
  return (
    <div data-testid="approval-plan" className="space-y-1">
      <p className="text-[11px] text-zinc-400">
        <span className="text-zinc-500">plan:</span> {n === 1 ? "1 step" : `${n} steps`}
      </p>
      <ol
        id={id}
        aria-label={n === 1 ? "The plan, 1 step" : `The plan, ${n} steps`}
        className="list-decimal space-y-0.5 pl-6 text-[12px] leading-relaxed text-zinc-300 marker:text-zinc-500"
      >
        {shown.map((goal, i) => (
          <li key={i} data-testid="approval-plan-step" className="whitespace-pre-wrap break-words pl-1">
            {goal || <span className="text-zinc-500">(no words)</span>}
          </li>
        ))}
      </ol>
      {part.more > 0 && (open || !capped) ? (
        <p data-testid="approval-plan-more" className="pl-6 text-[11px] text-zinc-500">
          …and {part.more === 1 ? "1 more step" : `${part.more} more steps`}
        </p>
      ) : null}
      {capped && (
        <MoreToggle
          open={open}
          onToggle={() => setOpen((v) => !v)}
          controls={id}
          closedLabel={`Show all ${part.steps.length} steps`}
          openLabel="Show fewer steps"
          testId="approval-plan-toggle"
        />
      )}
    </div>
  );
}

/** Code that wants to run (repl, run_code), verbatim and whole. */
function CodeLines({ code }: { code: string }) {
  return (
    <pre
      data-testid="approval-code"
      className="max-h-60 overflow-y-auto whitespace-pre-wrap break-all font-mono text-[12px] leading-relaxed text-zinc-200"
    >
      {code}
    </pre>
  );
}

export function ApprovalPreview({ tool, args }: { tool: string; args: Record<string, unknown> }) {
  const model = useMemo(() => approvalPreview(tool, args), [tool, args]);
  const [fullArgs, setFullArgs] = useState(false);
  const argsId = useId();
  const anyCut = model.parts.some((p) => p.type === "arg" && p.entry.cut);
  if (model.parts.length === 0) return null;
  return (
    <div data-testid="approval-preview" data-preview={model.kind} className="space-y-1.5">
      <div id={argsId} className="space-y-1.5">
        {model.parts.map((p, i) => {
          switch (p.type) {
            case "arg":
              return <ArgLine key={i} entry={p.entry} full={fullArgs} />;
            case "diff":
              return <DiffBlock key={i} part={p} />;
            case "content":
              return <ContentBlock key={i} lines={p.lines} />;
            case "command":
              return <CommandLine key={i} command={p.command} />;
            case "code":
              return <CodeLines key={i} code={p.code} />;
            case "plan":
              return <PlanSteps key={i} part={p} />;
          }
        })}
      </div>
      {anyCut && (
        <MoreToggle
          open={fullArgs}
          onToggle={() => setFullArgs((v) => !v)}
          controls={argsId}
          closedLabel="Show in full"
          openLabel="Show less"
          testId="approval-args-more"
        />
      )}
    </div>
  );
}
