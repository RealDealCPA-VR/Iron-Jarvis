"use client";

/**
 * "Install Codex for me" UNDER A CHAT REPLY (v1.455.0, readiness Wave G,
 * item 23).
 *
 * A chat message that asks to install Claude Code or Codex gets ONE page door
 * (daemon/page_doors, steps `install_claude_code` / `install_codex`) carrying
 * `install: "claude" | "codex"`. It is drawn through the ONE install press
 * (lib/cliInstall `useCliInstall` — the same job Settings › Connections and a
 * new chat's doors start): a Build terminal running the vendor's own
 * installer, every step on screen. The press is the consent; nothing is
 * installed before it. After it, the door says what is happening in the
 * same words the Connections row uses, and a refusal shows the daemon's own
 * sentence.
 */

import { Download } from "lucide-react";
import { DOOR_PILL } from "@/components/chat/doorStyle";
import { INSTALL_JOBS, installLabel, installingLine, useCliInstall } from "@/lib/cliInstall";

/** The CLI's name for each install press (lib/cliInstall's ids). */
export const INSTALL_NAMES: Readonly<Record<string, string>> = { claude: "Claude Code", codex: "Codex" };

/** Is `cli` an install press this dashboard has? */
export function isInstallPress(cli: unknown): cli is string {
  return typeof cli === "string" && Object.prototype.hasOwnProperty.call(INSTALL_JOBS, cli);
}

export function InstallDoorPress({ cli, onLaunched }: { cli: string; onLaunched?: () => void }) {
  const { install, start } = useCliInstall(() => onLaunched?.());
  if (!isInstallPress(cli)) return null;
  const name = INSTALL_NAMES[cli] ?? cli;
  const state = install[cli] ?? {};
  if (state.paneId !== undefined && !state.error) {
    return (
      <span role="status" data-testid={`door-install-${cli}-started`} className="text-[11px] text-zinc-400">
        {installingLine(name)}
      </span>
    );
  }
  return (
    <span className="inline-flex max-w-full flex-wrap items-center gap-1.5">
      <button
        type="button"
        data-testid={`door-install-${cli}`}
        disabled={state.busy === true}
        onClick={() => void start(cli)}
        className={DOOR_PILL}
      >
        <Download size={11} className="shrink-0 text-accent-soft/70" />
        <span className="truncate">{installLabel(name)}</span>
      </button>
      {state.error ? (
        <span role="alert" data-testid={`door-install-${cli}-error`} className="text-[11px] text-tone-danger">
          {state.error}
        </span>
      ) : null}
    </span>
  );
}
