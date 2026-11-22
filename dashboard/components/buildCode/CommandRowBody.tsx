"use client";

/**
 * One command's row in Build › Code's `/` menu: how it is typed, its argument
 * hint, what it does and (Pi) what kind it is. Shared by the start box and a
 * session's box, so the two menus read the same.
 */

import { COMMAND_SOURCE_WORDS, type ToolCommand } from "@/lib/buildApp";

export function CommandRowBody({ cmd, hint }: { cmd: ToolCommand; hint?: string }) {
  return (
    <>
      <span className="shrink-0 font-mono">
        {cmd.prefix ?? "/"}
        {cmd.name}
      </span>
      {hint ? (
        <span data-testid="code-command-hint" className="shrink-0 font-mono text-[12px] text-zinc-500">
          {hint}
        </span>
      ) : null}
      <span className="min-w-0 truncate text-[12px] text-zinc-500">
        {cmd.note ? `${cmd.description}: ${cmd.note}` : cmd.description}
      </span>
      {cmd.source ? (
        <span data-testid="code-command-source" className="ml-auto shrink-0 text-[11px] text-zinc-500">
          {COMMAND_SOURCE_WORDS[cmd.source]}
        </span>
      ) : null}
    </>
  );
}

export default CommandRowBody;
