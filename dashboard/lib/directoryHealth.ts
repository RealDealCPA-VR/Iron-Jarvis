/**
 * One reading of a Directory card's health (v1.358.0, lane 8D).
 *
 * The card already told the truth about itself — green only for a saved app
 * that works (v1.351.0, G08), "Off" for one the user switched off (v1.356.0,
 * G78), "Needs attention" for one that did not start — while the page's
 * "N connected" count and its connected-only filter read the raw `connected`
 * flag, which stays true for every SAVED app (connectors/service.py: a saved
 * connection is real and survives restarts). So the count said "3 connected"
 * over cards reading Off and Needs attention. The card and the page now read
 * this ONE function:
 *
 *  - `working`   — saved and working: green (`status === "connected"`);
 *  - `signin`    — saved and loaded, but its own sign-in has not landed yet
 *                  (Microsoft 365: `status === "signin"`);
 *  - `attention` — saved, but not working (did not start, no tools, a key
 *                  that was checked and refused, an expired sign-in …);
 *  - `off`       — saved and switched off by the user (its key kept);
 *  - `none`      — not connected here (or a link card).
 *
 * Dependency-free (no lib/api import — ~70 test files mock it wholesale).
 */

export type CardHealth = "working" | "signin" | "attention" | "off" | "none";

export interface HealthFacts {
  connect_via?: string | null;
  connected?: boolean | null;
  status?: string | null;
  /** false = switched off by the user (absent = on). */
  enabled?: boolean | null;
}

export function cardHealth(c: HealthFacts): CardHealth {
  if (!c.connected) return "none";
  // Only an app that runs as an extension has the switch (DirectoryPage's
  // own rule since v1.356.0).
  if (c.connect_via === "mcp" && (c.enabled === false || c.status === "off")) return "off";
  if (c.status === "connected") return "working";
  if (c.status === "signin") return "signin";
  return "attention";
}

export interface HealthCounts {
  /** Saved and working — the "N connected" the summary line says. */
  working: number;
  /** Saved and switched off. */
  off: number;
  /** Saved and not working, a sign-in still to finish included. */
  attention: number;
}

export function healthCounts(cards: readonly HealthFacts[]): HealthCounts {
  const out: HealthCounts = { working: 0, off: 0, attention: 0 };
  for (const c of cards) {
    const h = cardHealth(c);
    if (h === "working") out.working += 1;
    else if (h === "off") out.off += 1;
    else if (h === "signin" || h === "attention") out.attention += 1;
  }
  return out;
}

/** The summary line's words for what is saved but not working. */
export function attentionWords(n: number): string {
  return `${n} need${n === 1 ? "s" : ""} attention`;
}

/** v1.358.0 (lane 8D): what a key saved on an API-key card says, from the
 *  daemon's save answer (`test: {ok, detail}` — the Connections page's own
 *  check, run on save). An older daemon sends no `test`: the old words. */
export function keySaveNote(
  name: string,
  test: { ok?: unknown; detail?: unknown } | null | undefined,
): { ok: boolean; text: string } {
  if (!test || typeof test !== "object" || typeof test.ok !== "boolean") {
    return { ok: true, text: `${name} connected.` };
  }
  const detail = typeof test.detail === "string" ? test.detail.trim() : "";
  if (test.ok) return { ok: true, text: `${name} connected. The key works.` };
  return {
    ok: false,
    text: `Saved. ${detail || `${name} didn't accept this key.`}`,
  };
}
