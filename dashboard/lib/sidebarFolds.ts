/**
 * v1.537.0 — the owner: the sidebar's "Projects" (under the chats) and Chat ›
 * Code's "Terminals" should be collapsed by default. Each folds to its
 * heading with one press, says what it holds while folded (a count, or how
 * many are working), and the owner's choice is kept in this browser the way
 * "Other tools" keeps its own (lib/otherTools FOLDED_KEY).
 *
 * A part the owner never opened or folded starts FOLDED. A choice the owner
 * made always wins over that.
 */
import { useEffect, useState } from "react";

export const SIDEBAR_FOLDS_KEY = "ij_sidebar_folds";

/** The folding parts, and how each starts when the owner never picked. */
export type SidebarFold = "projects" | "terminals";
export const SIDEBAR_FOLDED_BY_DEFAULT: Record<SidebarFold, boolean> = {
  projects: true,
  terminals: true,
};

function stored(): Record<string, unknown> {
  const v: unknown = JSON.parse(window.localStorage.getItem(SIDEBAR_FOLDS_KEY) || "{}");
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

export function readSidebarFold(part: SidebarFold): boolean {
  try {
    const picked = stored()[part];
    return typeof picked === "boolean" ? picked : SIDEBAR_FOLDED_BY_DEFAULT[part];
  } catch {
    return SIDEBAR_FOLDED_BY_DEFAULT[part];
  }
}

export function writeSidebarFold(part: SidebarFold, folded: boolean): void {
  try {
    window.localStorage.setItem(SIDEBAR_FOLDS_KEY, JSON.stringify({ ...stored(), [part]: folded }));
  } catch {
    // Storage is a convenience: without it the part starts folded each time.
  }
}

/** `[folded, toggle]` for one part: starts as its default (so the server
 *  render and the first paint agree), then takes the owner's kept choice. */
export function useSidebarFold(part: SidebarFold): [boolean, () => void] {
  const [folded, setFolded] = useState(() => SIDEBAR_FOLDED_BY_DEFAULT[part]);
  useEffect(() => {
    setFolded(readSidebarFold(part));
  }, [part]);
  const toggle = () => {
    setFolded((was) => {
      writeSidebarFold(part, !was);
      return !was;
    });
  };
  return [folded, toggle];
}
