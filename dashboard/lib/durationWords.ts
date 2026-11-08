/**
 * The app's ONE clock vocabulary for a length of time (v1.449.0, plan 0h.5
 * item 10). "0.3 s", "12 s", "2 min 5 s", "2 min" — the words every settled
 * reply, run receipt and teammate card say. It lived in
 * components/chat/TurnReceipt (which still re-exports it) and is a plain
 * module here so a lib file that must stay free of component imports
 * (lib/buildApp, read by the bell) can say the same words: Build › Code's
 * receipt used to say "2 m 14 s" beside every other surface's "2 min 14 s".
 */

/** A duration in words: "0.3 s", "12 s", "2 min 5 s". Null for a value that
 *  is not a finite, non-negative number. */
export function secondsText(ms: number | null | undefined): string | null {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return null;
  const s = ms / 1000;
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 60) return `${Math.round(s)} s`;
  const whole = Math.round(s);
  const min = Math.floor(whole / 60);
  const rest = whole % 60;
  return rest ? `${min} min ${rest} s` : `${min} min`;
}
