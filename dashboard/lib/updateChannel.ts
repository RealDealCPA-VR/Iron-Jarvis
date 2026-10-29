/**
 * THE UPDATE CHANNEL (v1.405.0) — the Updates page's Stable / Fresh choice.
 *
 * The desktop app reads `update_channel` from config.toml at every update
 * check (desktop/main.js `updateChannelOf`); this page saves it through the
 * one settings writer (`PUT /settings/values`) and then asks for a check. The
 * desktop bridge's `channels()` (IPC `update:channels`) says which version each
 * channel offers right now.
 *
 * THE WAIT IS TIME. Nothing can see whether another PC ran a version, so the
 * words say "out for a day" and "you held it back" — never "tested on your
 * other PC". Pinned: __tests__/update-channel-v14050.test.tsx.
 */

export type UpdateChannel = "fresh" | "stable";

export const UPDATE_CHANNEL_KEY = "update_channel";

/** Each choice in one sentence (the spec's own words). */
export const CHANNEL_WORDS: Record<UpdateChannel, { label: string; sentence: string }> = {
  stable: {
    label: "Stable",
    sentence: "Gets a release once it has been out for a day with every test passing, unless you held it back.",
  },
  fresh: {
    label: "Fresh",
    sentence: "Gets every release as soon as it is published.",
  },
};

/** What `update:channels` answers (null fields = that branch could not be read). */
export interface ChannelVersions {
  channel: UpdateChannel;
  current: string | null;
  fresh: string | null;
  stable: string | null;
}

/** "stable" only for exactly that; anything else is Fresh (the shell's rule). */
export function asChannel(v: unknown): UpdateChannel {
  return String(v ?? "").trim().toLowerCase() === "stable" ? "stable" : "fresh";
}

function parts(v: string | null | undefined): [number, number, number] | null {
  const m = /^v?(\d+)\.(\d+)\.(\d+)$/.exec(String(v ?? "").trim());
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** -1 / 0 / 1 for two X.Y.Z versions; null when either is not one. */
export function compareVersions(a: string | null | undefined, b: string | null | undefined): number | null {
  const x = parts(a);
  const y = parts(b);
  if (!x || !y) return null;
  for (let i = 0; i < 3; i += 1) {
    if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1;
  }
  return 0;
}

/** This PC runs a version NEWER than Stable offers (it came from Fresh):
 *  on Stable it stays there until Stable passes it — it never goes back. */
export function aheadOfStable(current: string | null | undefined, stable: string | null | undefined): boolean {
  return compareVersions(current, stable) === 1;
}

/** Stable already offers this version (or a newer one), so a hold of it now
 *  would change nothing: Stable never moves to an older version. */
export function stableHasIt(current: string | null | undefined, stable: string | null | undefined): boolean {
  const c = compareVersions(stable, current);
  return c === 0 || c === 1;
}

/** "v1.404.0", or the words for a version that could not be read. */
export function versionWords(v: string | null | undefined): string {
  return parts(v) ? `v${String(v).replace(/^v/, "").trim()}` : "not known right now";
}

/** The sentence under the choice for a PC on Stable that runs a newer version. */
export function aheadWords(current: string, stable: string): string {
  return (
    `This PC runs ${versionWords(current)}, newer than Stable's ${versionWords(stable)} (it was on Fresh). ` +
    `It stays on ${versionWords(current)} until Stable passes it; it never goes back to an older version.`
  );
}

/** What GET /update/stable-hold answers. */
export interface HoldView {
  version: string;
  can_run: boolean;
  reason: string;
  command: string;
  actions_url: string;
}

export function asHoldView(raw: unknown): HoldView | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.command !== "string" || typeof r.version !== "string") return null;
  return {
    version: r.version,
    can_run: r.can_run === true,
    reason: typeof r.reason === "string" ? r.reason : "",
    command: r.command,
    actions_url: typeof r.actions_url === "string" ? r.actions_url : "",
  };
}
