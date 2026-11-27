"use client";

// What one "If" or "For each" did in a run (idea 09, phase 09-a): a For
// each's rows, one per item ("3 · invoice-03.pdf · Done · read 1,200.00"),
// and an If's path ("Took Then") with the steps on it. Read from the run's
// own record (the block's output and the path-keyed outputs of its steps),
// never worked out again here. Used by the canvas's live strip and by Run
// history on the Workflows page.

import { Badge } from "@/components/ui";
import { statusWords } from "@/lib/statusWords";
import { blockStatus, itemRows, tookWords } from "./blocks";

interface Out {
  status?: string;
  summary?: string;
  took?: string;
  data?: unknown;
}

export function BlockRunDetail({
  name,
  kind,
  steps,
  elseSteps,
  outputs,
  ended,
}: {
  name: string;
  kind: string;
  /** The block's own steps (For each), or its Then lane (If). */
  steps?: { name?: string }[];
  /** An If's Otherwise lane. */
  elseSteps?: { name?: string }[];
  outputs: Record<string, Out | undefined>;
  /** The run is over: a block still open reads Stopped. */
  ended: boolean;
}) {
  const out = outputs[name];
  if (kind === "for_each") {
    const rows = itemRows(out);
    if (!rows.length) return null;
    return (
      <ol data-testid="each-rows" className="mt-1.5 space-y-0.5 border-l hairline pl-3">
        {rows.map((r) => {
          const words = statusWords(r.status);
          return (
            <li key={r.n} data-testid={`each-row-${r.n}`} className="flex flex-wrap items-baseline gap-x-2 text-[12px]">
              <span className="w-6 shrink-0 text-right tabular-nums text-zinc-500">{r.n}</span>
              <span className="max-w-[16rem] truncate text-zinc-200" title={r.label}>
                {r.label}
              </span>
              <Badge variant="calm" value={words.word} tone={words.tone} keepCase />
              {r.summary ? (
                <span
                  className={`min-w-0 flex-1 truncate ${r.status === "failed" ? "text-tone-danger" : "text-zinc-500"}`}
                  title={r.summary}
                >
                  {r.summary}
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>
    );
  }
  if (kind !== "if") return null;
  const took = tookWords(out);
  if (!took) return null;
  const lane = out?.took === "then" ? steps ?? [] : elseSteps ?? [];
  const seg = out?.took === "then" ? "then" : "else";
  return (
    <div data-testid="if-took" className="mt-1.5 border-l hairline pl-3 text-[12px]">
      <p className="text-zinc-400">Took {took}</p>
      <ul className="mt-0.5 space-y-0.5">
        {lane.map((s, k) => {
          const nm = String(s.name ?? "").trim() || `step-${k + 1}`;
          const o = outputs[`${name}/${seg}/${nm}`];
          const st = blockStatus(o?.status, ended) || (ended ? "skipped" : "pending");
          const words = statusWords(st);
          return (
            <li key={`${nm}-${k}`} className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-zinc-200">{nm}</span>
              <Badge variant="calm" value={words.word} tone={words.tone} keepCase />
              {o?.summary ? (
                <span className="min-w-0 flex-1 truncate text-zinc-500" title={o.summary}>
                  {o.summary}
                </span>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
