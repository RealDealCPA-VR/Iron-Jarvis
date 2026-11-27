"use client";

/**
 * The intelligence plan, release 5: ONE quiet line under a reply (or a
 * task's result) that made or changed a file, saying what Iron Jarvis itself
 * checked: "Checked by Iron Jarvis: the spreadsheet opens, no error cells,
 * 3 totals add up." Pressing it opens the list: each check, what it found,
 * its result, and who checked ("by Iron Jarvis", or the model's name).
 *
 * Every word comes from the daemon's one record of checks (lib/checked
 * decodes it; this component composes none). A problem, and something
 * "worth a look" (what Iron Jarvis worked out itself and found different),
 * read in the warn tone; a pass, and what could not be checked, quietly. The
 * two keep their own words in the list ("Problem", "Worth a look"): the
 * colour is shared, the claim is not. The quiet line's TICK is drawn only
 * when nothing on it is "Not checked" (and no check is left uncounted in
 * the list): a line that says something was not checked opens with the
 * neutral mark, never with a tick. Never a notice,
 * never the bell: it is drawn where the reply already is, and a reply that
 * made no file gets none (the caller renders nothing for a null mark).
 *
 * Drawn once per mark (it arrives whole with the settled reply).
 */

import { memo, useId, useState } from "react";
import { Check, ChevronDown, ChevronRight, CircleDashed, TriangleAlert } from "lucide-react";
import { CHECKED_CLOSE, CHECKED_OPEN, isWarnResult, type CheckedItem, type CheckedMark } from "@/lib/checked";

function ItemIcon({ result }: { result: CheckedItem["result"] }) {
  if (isWarnResult(result)) return <TriangleAlert size={12} aria-hidden="true" className="mt-0.5 shrink-0 text-tone-warn" />;
  if (result === "passed") return <Check size={12} aria-hidden="true" className="mt-0.5 shrink-0 text-tone-success" />;
  return <CircleDashed size={12} aria-hidden="true" className="mt-0.5 shrink-0" />;
}

export const CheckedLine = memo(function CheckedLine({ mark }: { mark: CheckedMark | null }) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (!mark) return null;
  const warn = mark.tone === "warn";
  // A tick says "checked, and nothing was left out": some check passed, none
  // reads "Not checked", and the list holds every check (none is only counted).
  const ticked =
    mark.checks.some((c) => c.result === "passed") &&
    !mark.checks.some((c) => c.result === "not_checked") &&
    mark.more === 0;
  const canOpen = mark.checks.length > 0;
  const line = (
    <>
      {warn ? (
        <TriangleAlert size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-tone-warn" />
      ) : ticked ? (
        <Check size={13} aria-hidden="true" data-testid="checked-line-tick" className="mt-0.5 shrink-0 text-tone-success" />
      ) : (
        <CircleDashed size={13} aria-hidden="true" data-testid="checked-line-neutral" className="mt-0.5 shrink-0" />
      )}
      <span data-testid="checked-line-words" className="min-w-0 text-left [overflow-wrap:anywhere]">
        {mark.line}
      </span>
    </>
  );
  return (
    <div
      data-testid="checked-line"
      data-tone={mark.tone}
      className={`mt-1 grid min-w-0 gap-1 text-[12px] ${warn ? "text-tone-warn" : "text-zinc-500"}`}
    >
      {canOpen ? (
        <button
          type="button"
          data-testid="checked-line-press"
          aria-expanded={open}
          aria-controls={listId}
          title={open ? CHECKED_CLOSE : CHECKED_OPEN}
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 items-start gap-2 rounded-md py-0.5 text-left transition-colors hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent"
        >
          {line}
          {open ? (
            <ChevronDown size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-zinc-500" />
          ) : (
            <ChevronRight size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-zinc-500" />
          )}
          <span className="sr-only">{open ? CHECKED_CLOSE : CHECKED_OPEN}</span>
        </button>
      ) : (
        <p className="flex min-w-0 items-start gap-2">{line}</p>
      )}
      {open && canOpen ? (
        <ul id={listId} data-testid="checked-list" className="ml-5 grid min-w-0 gap-1 text-zinc-400">
          {mark.checks.map((c, i) => (
            <li
              key={`${i}-${c.label}`}
              data-testid="checked-item"
              data-result={c.result}
              className="flex min-w-0 items-start gap-2"
            >
              <ItemIcon result={c.result} />
              <span className="min-w-0 [overflow-wrap:anywhere]">
                <span className={isWarnResult(c.result) ? "text-tone-warn" : "text-zinc-300"}>{c.label}</span>
                {c.said ? <span>: {c.said}</span> : null}
                <span className="text-zinc-500">
                  {" "}
                  ({[c.resultWords, c.who].filter(Boolean).join(", ")})
                </span>
              </span>
            </li>
          ))}
          {mark.more > 0 && mark.moreWords ? (
            <li data-testid="checked-more" className="ml-5 text-zinc-500">
              {mark.moreWords}
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
});

export default CheckedLine;
