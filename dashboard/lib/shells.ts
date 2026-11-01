/**
 * The New terminal shell menu (v1.422.0, audit gap B6).
 *
 * The daemon lists its shells DEFAULT FIRST and names the default
 * (`GET /terminals/shells` → `{shells, default}`): a real PowerShell 7 when
 * one is installed, else Windows PowerShell — never the Microsoft Store copy
 * of PowerShell 7, whose start went through package activation and hung in
 * the v1.407 / v1.409 / v1.419 freezes. The Store copy stays in the menu
 * (the user can still pick it), labelled for what it is. Each row's words
 * are its `label` ("Windows PowerShell"); an older daemon sends none, and the
 * name is shown exactly as before.
 *
 * Import-free (no lib/api), like lib/buildLinks.
 */

import type { Shell } from "@/lib/types";

/** A shell row as the daemon sends it from v1.422.0. */
export interface ShellOption extends Shell {
  /** The menu's words ("PowerShell 7", "Windows PowerShell", …). */
  label?: string;
  /** The Microsoft Store copy of PowerShell 7 (pickable, never the default). */
  store?: boolean;
}

/** The title on the Store copy's row: why it is not the default. */
export const STORE_SHELL_TITLE =
  "The Microsoft Store copy of PowerShell 7 can take a long time to start, or not start at all. New terminals use another shell unless you pick this one.";

/** The words a row shows. */
export function shellLabel(s: ShellOption): string {
  const label = typeof s.label === "string" ? s.label.trim() : "";
  return label || s.name;
}

/** The shell a new terminal uses: the daemon's named default when it is in
 *  the list, else the first row (the daemon lists its default first), else
 *  "" (the daemon picks). */
export function defaultShellName(shells: ShellOption[], named?: string): string {
  if (named && shells.some((s) => s.name === named)) return named;
  return shells[0]?.name ?? "";
}
