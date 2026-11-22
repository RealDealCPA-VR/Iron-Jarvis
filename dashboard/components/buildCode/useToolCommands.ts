"use client";

/**
 * v1.557.0: a coding tool's `/` list for Build › Code's two boxes (the start
 * box, before a session exists, and a session's own box).
 *
 * The owner: "In the build > code area, the / commands for skills dont work.
 * These should be available at any point within this prompting window."
 *
 * WHY a hook: Claude Code's list is read per folder in the background by the
 * daemon (`hosted/claude_catalog`), so the first answer can be only its
 * built-in commands, or nothing, with `reading: true`. A box that asked once
 * and kept that answer never showed the user's own skills. This reads the
 * list each time the menu OPENS and looks again, a few times, while the
 * daemon says it is still reading.
 */

import { useEffect, useState } from "react";

import { get } from "@/lib/api";
import { decodeCommands, type ToolCommand } from "@/lib/buildApp";
import { commandHints } from "@/lib/buildAppHistory";

/** How long between two looks while the list is still being read. */
export const COMMANDS_LOOK_AGAIN_MS = 700;
/** How many more looks one opening of the menu may make. */
export const COMMANDS_LOOKS = 12;

export interface ToolCommandsState {
  /** What the daemon listed last (null: not answered yet, or unreadable). */
  commands: ToolCommand[] | null;
  /** Each command's argument hint. */
  hints: Record<string, string>;
  /** The daemon is still reading the list (and this is still looking). */
  reading: boolean;
}

const NOTHING: ToolCommandsState = { commands: null, hints: {}, reading: false };

function saysReading(raw: unknown): boolean {
  return !!raw && typeof raw === "object" && (raw as { reading?: unknown }).reading === true;
}

/** The words under the box when the `/` menu has no row to show: the list is
 *  still being read, or the tool listed nothing. null = say nothing. */
export function slashStatusWords(label: string, reading: boolean, listed: number): string | null {
  if (listed > 0) return null;
  if (reading) return `Reading ${label}'s commands…`;
  return `${label} listed no commands. A command you type is still sent to ${label} as typed.`;
}

/**
 * `wanted`: the `/` menu is open. `path`: the GET that lists the commands
 * (null = nothing to ask). `scope`: anything else the list belongs to (the
 * session's tool: a list read for another tool is never shown).
 */
export function useToolCommands(wanted: boolean, path: string | null, scope: string = ""): ToolCommandsState {
  const key = path === null ? null : `${scope}|${path}`;
  const [state, setState] = useState<{ key: string | null } & ToolCommandsState>({ key: null, ...NOTHING });

  useEffect(() => {
    if (!wanted || !path || key === null) return;
    let gone = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let looks = 0;
    const read = () => {
      get<unknown>(path)
        .then((raw) => {
          if (gone) return;
          const rows = decodeCommands(raw);
          const again = rows !== null && saysReading(raw) && looks < COMMANDS_LOOKS;
          setState({ key, commands: rows, hints: commandHints(raw), reading: again });
          if (again) {
            looks += 1;
            timer = setTimeout(read, COMMANDS_LOOK_AGAIN_MS);
          }
        })
        .catch(() => {
          // An older daemon, or one that is away: what was listed stays.
          if (!gone) setState((cur) => (cur.key === key ? { ...cur, reading: false } : { key, ...NOTHING, commands: [] }));
        });
    };
    read();
    return () => {
      gone = true;
      if (timer) clearTimeout(timer);
    };
  }, [wanted, path, key]);

  return state.key === key && key !== null ? state : NOTHING;
}
