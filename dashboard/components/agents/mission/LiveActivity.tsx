"use client";

// LIVE ACTIVITY (v1.307.0): the team's work as a log written for a person —
// "Researcher read k1.pdf", "Jarvis asked Writer to draft the strategy" — the
// daemon composes every line from the ledger (agents/mission.py), so this
// component only lays it out. It follows the newest line while the user is at
// the bottom, and stays put when they scroll up to read.
//
// v1.329.0 (calm chat wave 8, J4): a plain section with a quiet label over a
// hairline, not a card.

import { memo, useEffect, useRef } from "react";
import { clock, type MissionActivity } from "@/lib/mission";

const DOT: Record<MissionActivity["tone"], string> = {
  ok: "bg-tone-success",
  warn: "bg-tone-danger",
  ask: "bg-tone-warn",
  info: "bg-accent",
};

function LiveActivityLog({ lines, running }: { lines: MissionActivity[]; running: boolean }) {
  const box = useRef<HTMLOListElement | null>(null);
  const pinned = useRef(true);
  useEffect(() => {
    const el = box.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [lines.length]);
  return (
    <section data-testid="mission-activity">
      <header className="flex items-center justify-between border-b hairline px-2 pb-1.5">
        <h2 className="text-[13px] font-medium text-zinc-400">Live activity</h2>
        {running && (
          <span className="flex items-center gap-1.5 text-[11px] text-zinc-400">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" /> live
          </span>
        )}
      </header>
      {lines.length === 0 ? (
        <p className="px-2 py-3 text-[12px] text-zinc-500">
          {running ? "Getting started…" : "Nothing was recorded for this objective."}
        </p>
      ) : (
        <ol
          ref={box}
          onScroll={(e) => {
            const el = e.currentTarget;
            pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          }}
          className="max-h-56 space-y-1 overflow-y-auto px-2 py-2.5"
        >
          {lines.map((line, i) => (
            <li
              key={`${line.at ?? ""}-${i}`}
              data-tone={line.tone}
              className="flex items-start gap-2.5 text-[13px] leading-5 text-zinc-300"
            >
              <span className="w-16 shrink-0 whitespace-nowrap tabular-nums text-[11px] leading-5 text-zinc-500">{clock(line.at)}</span>
              <span className={`mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full ${DOT[line.tone]}`} />
              <span className="min-w-0 break-words">{line.text}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** Memoised (v1.309.0): the log redraws when its lines change, never because
 *  a token flush or an unchanged poll touched the screen around it. */
export const LiveActivity = memo(LiveActivityLog);
