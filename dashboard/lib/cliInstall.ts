"use client";

/**
 * Install Claude Code or Codex for me, without a terminal of my own
 * (v1.449.0, readiness Wave C item 20).
 *
 * The most common subscriber — a Claude Pro or ChatGPT Plus plan, no
 * command-line tool ever installed — used to be sent to a vendor web page
 * with a PowerShell command to paste. Now one press opens a Build terminal
 * that runs the vendor's OWN documented installer: `POST /terminals/launch
 * {cli: "claude-code-install" | "codex-install"}`, the same door the starter
 * model's download uses (lib/starterModel). The launch route accepts only
 * catalog ids, so this asks for a set-up job BY NAME
 * (`terminals/ai_clis.SETUP_LAUNCHES`) and never sends a command: nothing
 * here can type an arbitrary line into a shell. The click is the consent,
 * and the terminal shows the install as it happens — nothing is installed
 * silently. Once the tool is on the PC, the row's Sign in press (lib/cliSignIn)
 * takes over.
 *
 * `INSTALL_JOBS` is pinned equal to the daemon's catalog by
 * tests/test_cli_install_jobs_v14490.py.
 *
 * Its own file, not lib/api.ts, because ~70 test files mock that module.
 */

import { useState } from "react";
import { post } from "@/lib/api";

/** The set-up job per CLI (the Launch-catalog id of the tool it installs →
 *  the job's id; terminals/ai_clis.INSTALL_JOBS). */
export const INSTALL_JOBS: Readonly<Record<string, string>> = {
  claude: "claude-code-install",
  codex: "codex-install",
};

/** The press's words: "Install Claude Code for me". */
export function installLabel(name: string): string {
  return `Install ${name} for me`;
}

/** v1.449.0: the check-now press after an install, and what the row says
 *  when the tool still is not found after a fresh look. */
export const INSTALLED_CHECK_LABEL = "I've installed it — check now";
export function stillNotInstalledLine(name: string): string {
  return `${name} still isn't found on this PC. Wait for the Build terminal to say the install finished, then press “${INSTALLED_CHECK_LABEL}” again.`;
}

/** What the row says while the terminal installs. */
export function installingLine(name: string): string {
  return `A Build terminal opened and is installing ${name} — every step shows there, nothing runs hidden. When it says it finished, this row finds ${name} by itself within a minute, or press “${INSTALLED_CHECK_LABEL}”.`;
}

/** Open the install terminal for CLI `cli` ("claude" | "codex"). Resolves the
 *  new pane's id (null when the daemon did not say); rejects with the daemon's
 *  own sentence. An unknown CLI rejects before anything is asked. */
export async function launchInstall(cli: string): Promise<string | null> {
  const job = INSTALL_JOBS[cli];
  if (!job) throw new Error(`Iron Jarvis has no installer for ${cli}.`);
  const res = await post<{ id?: string }>("/terminals/launch", { cli: job });
  return res && typeof res.id === "string" ? res.id : null;
}

/** One CLI's install press: in flight, the pane it opened (`null` = opened,
 *  id unknown), or the daemon's refusal sentence. */
export interface CliInstallState {
  busy?: boolean;
  paneId?: string | null;
  error?: string;
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The per-CLI install press, as a hook (the lib/cliSignIn shape). */
export function useCliInstall(onLaunched?: (cli: string) => void) {
  const [install, setInstall] = useState<Record<string, CliInstallState>>({});

  async function start(cli: string) {
    setInstall((s) => ({ ...s, [cli]: { busy: true } }));
    try {
      const paneId = await launchInstall(cli);
      setInstall((s) => ({ ...s, [cli]: { paneId } }));
      onLaunched?.(cli);
    } catch (e) {
      setInstall((s) => ({ ...s, [cli]: { error: errText(e) } }));
    }
  }

  return { install, start };
}
