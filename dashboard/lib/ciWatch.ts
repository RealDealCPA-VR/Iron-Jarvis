/**
 * A coding session's pull-request watch, said in ONE quiet line (v1.439.0,
 * chat-first Wave 6). `GET /build/sessions/{id}/ci` (daemon/routes/ci_watch.py)
 * answers `{watch, on}`: `watch` is null until Iron Jarvis has seen the
 * session's pull request, else `{state, rounds, max_rounds, failed, words,
 * next_check_at, …}`. No GitHub call is made by that read.
 *
 * The line is shown only while a watch exists and says something; a closed
 * pull request says nothing. The daemon's own words win for the states it
 * explains (gave up, waiting for you, stopped).
 */

import { get } from "@/lib/api";

export interface CiWatch {
  state: string;
  /** Fix rounds started in this failing streak. */
  rounds: number;
  maxRounds: number;
  failed: string[];
  /** The daemon's own sentence about the last step ("" when none). */
  words: string;
  /** When the checks are read next (a Date), or null. */
  nextCheckAt: Date | null;
}

export interface CiView {
  watch: CiWatch | null;
  /** The `ci_autofix` setting: false = nothing is watched or fixed. */
  on: boolean;
}

function str(v: unknown, max = 600): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0;
}

/** The daemon writes UTC without a zone (SQLite drops tzinfo): read it as UTC. */
export function utcMoment(v: unknown): Date | null {
  const s = str(v, 64).trim();
  if (!s) return null;
  const zoned = /([zZ]|[+-]\d\d:?\d\d)$/.test(s) ? s : `${s}Z`;
  const d = new Date(zoned);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Whitelist decoder: anything unexpected reads as "no watch". */
export function decodeCiView(raw: unknown): CiView {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const on = r.on !== false;
  const w = r.watch;
  if (!w || typeof w !== "object") return { watch: null, on };
  const o = w as Record<string, unknown>;
  const state = str(o.state, 40);
  if (!state) return { watch: null, on };
  return {
    on,
    watch: {
      state,
      rounds: num(o.rounds),
      maxRounds: num(o.max_rounds) || 3,
      failed: Array.isArray(o.failed) ? o.failed.filter((x): x is string => typeof x === "string").slice(0, 20) : [],
      words: str(o.words, 600),
      nextCheckAt: utcMoment(o.next_check_at),
    },
  };
}

export async function getCiView(sessionId: string): Promise<CiView> {
  return decodeCiView(await get<unknown>(`/build/sessions/${encodeURIComponent(sessionId)}/ci`));
}

function clock(d: Date): string {
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function roundsLeft(w: CiWatch): string {
  const left = Math.max(0, w.maxRounds - w.rounds);
  return `${left} fix round${left === 1 ? "" : "s"} left`;
}

/** The tone of the line: a stop the user should read is amber. */
export function ciTone(w: CiWatch): "quiet" | "warn" {
  return w.state === "gave_up" || w.state === "asking" || w.state === "stopped" ? "warn" : "quiet";
}

/**
 * The one line, or "" when there is nothing to say (no watch, a closed pull
 * request). `now` decides whether the next look is still ahead.
 */
export function ciWatchWords(view: CiView, now: Date = new Date()): string {
  const w = view.watch;
  if (!w || w.state === "closed") return "";
  const active = w.state === "watching" || w.state === "fix_due" || w.state === "fixing" || w.state === "reviewing";
  if (active && !view.on) {
    return "Fixing failed checks is turned off in Settings, so Iron Jarvis is not watching this pull request.";
  }
  // The daemon's own sentence for a stop it explains; the lead only when it said none.
  const own = (lead: string) => w.words || lead;
  switch (w.state) {
    case "watching": {
      const next =
        w.nextCheckAt && w.nextCheckAt.getTime() > now.getTime()
          ? ` Next look at ${clock(w.nextCheckAt)}.`
          : "";
      const left = w.rounds > 0 ? ` ${roundsLeft(w)}.` : "";
      return `Iron Jarvis is watching its checks.${next}${left}`;
    }
    case "fix_due":
      return `A check failed. Iron Jarvis starts a fix when this session is free (${roundsLeft(w)}).`;
    case "fixing":
      return `Iron Jarvis is fixing a failed check (round ${Math.max(1, w.rounds)} of ${w.maxRounds}).`;
    case "reviewing":
      return `A reviewer is reading the fix (round ${Math.max(1, w.rounds)} of ${w.maxRounds}).`;
    case "passing":
      return "Its checks pass. Iron Jarvis watches again after the next push.";
    case "gave_up":
      return own("Iron Jarvis stopped fixing its checks.");
    case "asking":
      return own("A fix is waiting for you.");
    case "stopped":
      return own("The watch stopped.");
    default:
      return "";
  }
}
