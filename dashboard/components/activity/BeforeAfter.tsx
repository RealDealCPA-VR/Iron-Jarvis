"use client";

/**
 * BEFORE AND AFTER for one change (next leaps 40-a, D-40.2): read on this PC
 * from Iron Jarvis's own copy of how it was, beside how it is now, and never
 * sent anywhere (`GET /changes/{id}/before-after`).
 *
 *  - A text file: its line-by-line difference (removed lines in red, added
 *    in green), one column, scrolling inside its own box.
 *  - Anything else (a setting, a key, a record, a memory note, a file that
 *    is not text): two sides, **Before** and **Now**, side by side on a wide
 *    screen and STACKED at 390 px (`grid-cols-1 sm:grid-cols-2`, G11).
 *  - A key is named, never shown; a copy that is gone says so.
 */

import { useEffect, useState } from "react";
import { LoaderInline } from "@/components/ui";
import { readBeforeAfter, sizeWords, type BeforeAfterItem, type BeforeAfterView, type Side } from "@/lib/dayChanges";
import { clockTime } from "@/lib/format";

export const READ_HERE_LINE = "Read on this PC from Iron Jarvis's own copy. Never sent anywhere.";

function sideWords(side: Side, which: "before" | "now"): string {
  if (!side.kept) return "No copy of how it was is kept any more.";
  if (side.words) return side.words;
  if (!side.exists) return which === "before" ? "It was not there." : "It is not there now.";
  const bits = [sizeWords(side.size)];
  if (side.modified) bits.push(`changed ${clockTime(side.modified)}`);
  return bits.filter(Boolean).join(", ") || "There.";
}

function DiffLines({ lines, cut }: { lines: string[]; cut: boolean }) {
  return (
    <div data-testid="before-after-diff" className="min-w-0">
      <pre className="max-h-80 overflow-auto rounded-lg bg-white/[0.03] p-2 text-[12px] leading-relaxed">
        {lines.map((line, i) => {
          const tone = line.startsWith("+++") || line.startsWith("---")
            ? "text-zinc-500"
            : line.startsWith("+")
              ? "text-tone-success"
              : line.startsWith("-")
                ? "text-tone-danger"
                : line.startsWith("@@")
                  ? "text-zinc-500"
                  : "text-zinc-300";
          return (
            <div key={i} className={`whitespace-pre-wrap [overflow-wrap:anywhere] ${tone}`}>
              {line || " "}
            </div>
          );
        })}
      </pre>
      {cut && <p className="mt-1 text-[11px] text-zinc-500">The difference is long; the first part is shown.</p>}
    </div>
  );
}

function Sides({ item }: { item: BeforeAfterItem }) {
  return (
    <div data-testid="before-after-sides" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {(["before", "now"] as const).map((which) => {
        const side = which === "before" ? item.before : item.now;
        return (
          <div key={which} data-testid={`before-after-${which}`} className="min-w-0 rounded-lg bg-white/[0.03] p-2">
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
              {which === "before" ? "Before" : "Now"}
            </div>
            {side.text !== null && side.kept && side.exists ? (
              <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap text-[12px] leading-relaxed text-zinc-300 [overflow-wrap:anywhere]">
                {side.text || " "}
              </pre>
            ) : (
              <p className="mt-1 text-[12px] leading-relaxed text-zinc-300 [overflow-wrap:anywhere]">{sideWords(side, which)}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function BeforeAfter({ id }: { id: string }) {
  const [view, setView] = useState<BeforeAfterView | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setView(null);
    setError(null);
    readBeforeAfter(id)
      .then((v) => live && setView(v))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [id]);
  if (error) {
    return (
      <p data-testid="before-after-error" className="text-[12px] text-tone-warn [overflow-wrap:anywhere]">
        The before and after could not be read: {error}
      </p>
    );
  }
  if (!view) return <LoaderInline label="Reading the before and after…" />;
  return (
    <div data-testid="before-after" className="flex min-w-0 flex-col gap-2">
      {view.items.map((item, i) => (
        <div key={`${item.name}-${i}`} className="flex min-w-0 flex-col gap-1">
          {view.items.length > 1 && <div className="text-[12px] text-zinc-400 [overflow-wrap:anywhere]">{item.name}</div>}
          {item.before.text !== null || item.now.text !== null ? (
            // A memory note: its text, before and now.
            <Sides item={item} />
          ) : item.isText && item.diff.length > 0 ? (
            <>
              <DiffLines lines={item.diff} cut={item.cut} />
              <p className="text-[11px] text-zinc-500 [overflow-wrap:anywhere]">
                Before: {sideWords(item.before, "before")} Now: {sideWords(item.now, "now")}
              </p>
            </>
          ) : item.isText ? (
            <p className="text-[12px] text-zinc-400">No difference: it is as it was.</p>
          ) : (
            <Sides item={item} />
          )}
        </div>
      ))}
      {view.note && <p className="text-[12px] text-zinc-400 [overflow-wrap:anywhere]">{view.note}</p>}
      <p data-testid="before-after-here" className="text-[11px] text-zinc-500">{READ_HERE_LINE}</p>
    </div>
  );
}

export default BeforeAfter;
