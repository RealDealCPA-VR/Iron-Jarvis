/**
 * Old addresses of relocated pages (calm UI redesign S10, AUDIT Q8): each
 * redirects to its new home and FORWARDS its query, so a deep link that worked
 * before (`/connections?focus=endpoints`, `/channels?focus=add`) still lands on
 * the same control.
 */
export type SearchParams = Record<string, string | string[] | undefined>;

/** `/settings?section=<section>&<the old query>` (section wins on a clash). */
export function relocatedHref(base: string, params: SearchParams, extra: Record<string, string> = {}): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (k in extra) continue;
    if (Array.isArray(v)) v.forEach((x) => q.append(k, x));
    else if (typeof v === "string") q.append(k, v);
  }
  for (const [k, v] of Object.entries(extra)) q.set(k, v);
  const s = q.toString();
  return s ? `${base}?${s}` : base;
}

/** Where each relocated route now lives. */
export const RELOCATED: Readonly<Record<string, { base: string; extra: Record<string, string> }>> = {
  "/connections": { base: "/settings", extra: { section: "connections-accounts" } },
  "/marketplace": { base: "/settings", extra: { section: "connections-apps" } },
  "/channels": { base: "/settings", extra: { section: "connections-notifications" } },
  "/secrets": { base: "/settings", extra: { section: "connections-secrets" } },
  "/computeruse": { base: "/settings", extra: { section: "connections-browser" } },
  "/tools": { base: "/settings", extra: { section: "permissions-tools" } },
  "/you": { base: "/settings", extra: { section: "memory-profile" } },
  "/train": { base: "/settings", extra: { section: "memory-train" } },
  "/updates": { base: "/settings", extra: { section: "system-updates" } },
  "/kanban": { base: "/sessions", extra: { view: "board" } },
};
