"use client";

// How long the running chat turn has taken, and how long it has been quiet
// (v1.246.0). The user's report was a chat that "gets hung up from time to
// time" — and a turn that is working looked exactly like one that was stuck:
// the same pulsing "Thinking…" for four seconds or four minutes. These two
// labels are the difference, drawn in the bubble's existing muted style.
//
// Each one TICKS ITSELF, so the chat page (thousands of lines) never
// re-renders once a second just to move a number. v1.348.0: the live work
// line ("Working for 12 s · read 2 files") ticks the same way, with useNow.

import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

/** 12000 → "12s", 65000 → "1:05". */
export function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

/** v1.348.0: a running turn's time in the words the folded line uses
 *  ("Worked for 14 s"), in WHOLE seconds so a ticking line never flickers
 *  tenths: 12000 → "12 s", 65000 → "1 min 5 s", 120000 → "2 min". Null under
 *  a second (or for junk): "Working for 0 s" says nothing. */
export function elapsedWords(ms: number | null | undefined): string | null {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 1000) return null;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s} s`;
  const min = Math.floor(s / 60);
  const rest = s % 60;
  return rest ? `${min} min ${rest} s` : `${min} min`;
}

/** The current time, re-read once a second while `active`. Whatever calls it
 *  re-renders ALONE each tick, so keep it in a small leaf (v1.348.0: exported
 *  for the live work line in components/chat/WorkLine). */
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}

/** Elapsed time since `since`, shown only once the turn has taken longer than
 *  `after` ms — a fast turn never shows a clock at all. */
export function TurnClock({
  since,
  after = 3000,
}: {
  since: number | null;
  after?: number;
}) {
  const now = useNow(since != null);
  if (since == null) return null;
  const ms = now - since;
  if (ms < after) return null;
  return (
    <span data-testid="turn-clock" className="tabular-nums text-xs text-zinc-500">
      {formatElapsed(ms)}
    </span>
  );
}

/** Under text that has already streamed: once nothing new has arrived for
 *  `after` ms (a tool running, the model thinking between rounds), say the
 *  turn is still working and for how long it has been quiet. */
export function QuietNote({
  since,
  after = 8000,
}: {
  since: number | null;
  after?: number;
}) {
  const now = useNow(since != null);
  if (since == null) return null;
  const ms = now - since;
  if (ms < after) return null;
  return (
    <div
      data-testid="turn-quiet"
      className="mt-1.5 inline-flex items-center gap-2 text-xs text-zinc-500"
    >
      <Loader2 size={12} className="animate-spin text-accent-soft" />
      <span>Still working · {formatElapsed(ms)}</span>
    </div>
  );
}
