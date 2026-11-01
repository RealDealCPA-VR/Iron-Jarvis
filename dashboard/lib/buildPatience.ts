/**
 * A CODING REPLY WAITS OUT A PASSING PROVIDER ERROR (v1.422.0, Wave 2 "Never
 * lose work", B1 — the daemon's `harness/patience`).
 *
 * When a round's model call fails for a moment (the provider is overloaded or
 * busy, a server error, a timeout, a dropped connection), a coding session's
 * reply no longer ends: the daemon asks the SAME model again after 5, 15, 30
 * and 60 seconds (a server's own "try again in" honoured), and says so on the
 * live line with a `provider_wait` frame:
 *
 *   {state: "waiting", words, attempt, of, wait_s, why, provider}
 *   {state: "trying", attempt, of}        — the wait is over, it asks again
 *
 * The words are the daemon's ("Claude Code is busy (overloaded). Trying again
 * in 30 s, attempt 2 of 5."); this module only reads them.
 *
 * Pure; no React, no fetch.
 */

/** The live line's SSE event name (`harness/patience.FRAME`). */
export const PROVIDER_WAIT_FRAME = "provider_wait";

/** One wait the live line shows. */
export interface ProviderWait {
  words: string;
  attempt: number;
  of: number;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.floor(v) : null;
}

/** A `provider_wait` frame's data: the wait to show, "trying" when the wait
 *  is over (the line goes), or null for anything else. */
export function decodeProviderWait(data: unknown): ProviderWait | "trying" | null {
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const d = data as Record<string, unknown>;
  if (d.state === "trying") return "trying";
  if (d.state !== "waiting") return null;
  const words = typeof d.words === "string" ? d.words.trim().slice(0, 300) : "";
  const attempt = num(d.attempt);
  const of = num(d.of);
  if (!words || attempt === null || of === null) return null;
  return { words, attempt, of };
}
