/**
 * "Open in Code view" on a Build terminal (v1.409.0): a terminal where
 * Claude Code or Codex ran, and has EXITED (the terminal is back at its
 * shell prompt), can carry the same conversation on in Build's Code view
 * (`POST /build/app/from-terminal/{pane_id}`). Two processes on one saved
 * conversation would corrupt it, so the offer waits for the tool to be gone.
 *
 * WHAT COUNTS AS EXITED, in order:
 *  - the daemon says so on the terminal's activity row or the terminal
 *    itself (`can_open_in_code: true`, or `harness_exited: true` — read as
 *    given, never guessed; `false` hides the offer);
 *  - else a terminal reopened after a restart whose tool died with the old
 *    Iron Jarvis (`resume_cli` names claude or codex: the shell is fresh).
 * A plain terminal, or one whose tool may still be running, shows nothing:
 * the Terminal view stays exactly as it was. The daemon decides in the end
 * (a refusal is shown in its words).
 *
 * Pure and import-free (the Build page reads it on every render).
 */

export interface CodeViewOffer {
  paneId: string;
  /** "Claude Code" or "Codex". */
  toolName: string;
}

function toolOf(cli: unknown): string | null {
  const c = typeof cli === "string" ? cli.trim().toLowerCase() : "";
  if (!c) return null;
  if (c === "claude" || c.startsWith("claude")) return "Claude Code";
  if (c === "codex" || c.startsWith("codex")) return "Codex";
  return null;
}

function flag(row: Record<string, unknown> | null | undefined): boolean | null {
  if (!row) return null;
  for (const k of ["can_open_in_code", "harness_exited"]) {
    if (row[k] === true) return true;
    if (row[k] === false) return false;
  }
  return null;
}

export function codeViewOfferFor(
  terminal: { id: string; alive?: boolean; resume_cli?: string | null } | null | undefined,
  activity: { agent_cli?: string | null; state?: string | null } | null | undefined,
  cliOverride?: string | null,
): CodeViewOffer | null {
  if (!terminal || terminal.alive === false) return null;
  const said = flag(activity as Record<string, unknown> | null) ?? flag(terminal as unknown as Record<string, unknown>);
  if (said === false) return null;
  const state = activity?.state ?? "";
  if (state === "working" || state === "blocked") return null;
  if (said === true) {
    const name = toolOf(cliOverride) ?? toolOf(activity?.agent_cli) ?? toolOf(terminal.resume_cli);
    return name ? { paneId: terminal.id, toolName: name } : null;
  }
  const restored = toolOf(terminal.resume_cli);
  return restored ? { paneId: terminal.id, toolName: restored } : null;
}

/** The offer's line ("Claude Code has finished in this terminal."). */
export function codeViewOfferWords(o: CodeViewOffer): string {
  return `${o.toolName} has finished in this terminal. Carry the same conversation on with buttons and cards instead of typing.`;
}
