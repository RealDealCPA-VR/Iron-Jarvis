// v1.280.0 — the Build page's per-pane storage is pruned against the panes
// that exist.
//
// Three families of localStorage keys are written per pane id and were never
// removed: `ij.pane.view.<id>` (terminal vs chat), `ij.pane.thread.<id>` (the
// pane's chat thread) and the entries of `ij_term_layout` (the canvas rect
// map). A pane closed by the user, by an agent, or by a shell that exited left
// its keys behind forever — over the life of an install the map and the key
// list only grew. Pane ids survive a daemon restart (the snapshot restores a
// pane under its original id), so the live list the daemon answers is the
// truth: anything not in it is a pane nobody can see again.
//
// Called ONLY after a successful `/terminals` answer. A daemon that is booting
// answers 503, not an empty list, so a transient outage never prunes anything.

/** Prefixes of the per-pane keys, exactly as the writers spell them. */
export const PANE_VIEW_PREFIX = "ij.pane.view.";
export const PANE_THREAD_PREFIX = "ij.pane.thread.";
/** The canvas rect map: `{ [paneId]: Rect }`. */
export const LAYOUT_KEY = "ij_term_layout";

export interface PruneReport {
  /** Per-pane keys removed (view + thread). */
  keys: number;
  /** Entries dropped from the rect map. */
  rects: number;
}

/**
 * Remove every per-pane key and rect whose pane id is not in `liveIds`.
 * Pure over the given Storage; never throws (a private window that refuses
 * storage prunes nothing).
 */
export function prunePaneStorage(
  liveIds: readonly string[],
  storage: Storage | null | undefined = typeof window === "undefined" ? undefined : window.localStorage,
): PruneReport {
  const report: PruneReport = { keys: 0, rects: 0 };
  if (!storage) return report;
  const live = new Set(liveIds);
  try {
    const dead: string[] = [];
    for (let i = 0; i < storage.length; i += 1) {
      const key = storage.key(i);
      if (!key) continue;
      const prefix = key.startsWith(PANE_VIEW_PREFIX)
        ? PANE_VIEW_PREFIX
        : key.startsWith(PANE_THREAD_PREFIX)
          ? PANE_THREAD_PREFIX
          : null;
      if (!prefix) continue;
      const id = key.slice(prefix.length);
      if (id && !live.has(id)) dead.push(key);
    }
    for (const key of dead) {
      storage.removeItem(key);
      report.keys += 1;
    }
    const raw = storage.getItem(LAYOUT_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const map = parsed as Record<string, unknown>;
        const kept: Record<string, unknown> = {};
        for (const [id, rect] of Object.entries(map)) {
          if (live.has(id)) kept[id] = rect;
          else report.rects += 1;
        }
        if (report.rects > 0) storage.setItem(LAYOUT_KEY, JSON.stringify(kept));
      }
    }
  } catch {
    /* a refusing or corrupt store: leave it as it is */
  }
  return report;
}
