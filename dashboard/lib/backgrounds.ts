/**
 * Downloaded backgrounds on this PC (v1.410.0) — the dashboard side of the
 * daemon's `/backgrounds` routes (backgrounds/store.py).
 *
 * `listBackgrounds` / `downloadBackground` / `deleteBackground` call the
 * daemon with the token; every change fires `BACKGROUNDS_EVENT` on window so
 * the layer behind the app (components/BackgroundLayer) and Settings re-read.
 * `backgroundViewUrl` is the sealed-off address the iframe loads: the daemon
 * serves the saved file with a no-network CSP, WITHOUT the token (an iframe
 * cannot send it, and a sandboxed page can read its own address). The colours
 * ride the fragment, so they never reach the daemon either.
 */

import { API_BASE, del, get, post } from "./api";
import { isBackgroundId } from "./backgroundCatalog";
import type { ThemeVars } from "./themePalette";

export const BACKGROUNDS_EVENT = "ij-backgrounds-change";

export type BackgroundState = "absent" | "ready" | "outdated";

export interface BackgroundRow {
  id: string;
  name: string;
  size: number;
  state: BackgroundState;
  bytes: number;
}

export interface BackgroundList {
  backgrounds: BackgroundRow[];
  total_bytes: number;
}

const STATES = new Set<BackgroundState>(["absent", "ready", "outdated"]);

export function decodeBackgroundList(raw: unknown): BackgroundList {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const rows: BackgroundRow[] = [];
  for (const x of Array.isArray(r.backgrounds) ? r.backgrounds : []) {
    const o = (x && typeof x === "object" ? x : {}) as Record<string, unknown>;
    if (!isBackgroundId(o.id)) continue;
    rows.push({
      id: o.id,
      name: typeof o.name === "string" ? o.name : o.id,
      size: typeof o.size === "number" ? o.size : 0,
      state: STATES.has(o.state as BackgroundState) ? (o.state as BackgroundState) : "absent",
      bytes: typeof o.bytes === "number" ? o.bytes : 0,
    });
  }
  return { backgrounds: rows, total_bytes: typeof r.total_bytes === "number" ? r.total_bytes : 0 };
}

function changed() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(BACKGROUNDS_EVENT));
}

export async function listBackgrounds(): Promise<BackgroundList> {
  return decodeBackgroundList(await get<unknown>("/backgrounds"));
}

export async function downloadBackground(id: string): Promise<BackgroundRow> {
  const row = await post<BackgroundRow>(`/backgrounds/${encodeURIComponent(id)}/download`, {}, { timeoutMs: 30_000 });
  changed();
  return row;
}

export async function deleteBackground(id: string): Promise<number> {
  const res = await del<{ freed_bytes?: number }>(`/backgrounds/${encodeURIComponent(id)}`);
  changed();
  return typeof res?.freed_bytes === "number" ? res.freed_bytes : 0;
}

const tripletHex = (v: string | undefined, fallback: string): string => {
  const parts = (v ?? "").trim().split(/\s+/).map(Number);
  if (parts.length !== 3 || parts.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return fallback;
  return parts.map((n) => n.toString(16).padStart(2, "0")).join("");
};

/** The fragment a background reads its colours from. */
export function backgroundHash(vars: ThemeVars, scheme: "dark" | "light"): string {
  const accent = tripletHex(vars["accent-rgb"], "22d3ee");
  const second = tripletHex(vars["accent-deep-rgb"], accent);
  const bg = tripletHex(vars["ink-950"], "070809");
  return `#accent=${accent}&second=${second}&bg=${bg}&mode=${scheme}`;
}

export function backgroundViewUrl(id: string, hash: string): string {
  return `${API_BASE}/backgrounds/${encodeURIComponent(id)}/view${hash}`;
}
