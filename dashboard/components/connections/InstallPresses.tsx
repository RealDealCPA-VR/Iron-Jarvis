"use client";

/**
 * Settings › Connections catches up with the first-run door (v1.455.0,
 * readiness Wave H item 28).
 *
 * The chat's connect doors (components/onboarding/ConnectDoors) offered
 * "Install Claude Code / Codex for me" and "Ollama is running but has no
 * model yet — Download a starter model" since v1.449.0, while the SAME rows
 * on Settings › Connections still only linked the vendor's page and called a
 * running Ollama with no model "Not running on this PC". These two presses
 * are built on the doors' own pieces — the hooks and words of
 * `lib/cliInstall` (the vendor's own installer, `POST /terminals/launch
 * {cli: "claude-code-install" | "codex-install"}`) and `lib/starterModel`
 * (`{cli: "ollama-starter"}`) — so the two places can never say different
 * things or run different jobs. Nothing is installed or downloaded without
 * the press; the Build terminal shows every step.
 */

import { useState } from "react";
import Link from "next/link";
import { Loader2, RefreshCw, TerminalSquare } from "lucide-react";
import { post } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";
import {
  INSTALLED_CHECK_LABEL,
  INSTALL_JOBS,
  installLabel,
  installingLine,
  stillNotInstalledLine,
  useCliInstall,
} from "@/lib/cliInstall";
import { STARTER_MODEL_LABEL, useStarterModel } from "@/lib/starterModel";
import { paneHref } from "@/lib/cliSignIn";
import { CALM_ACTION } from "@/components/connections/endpointChip";

const ROW_LINK =
  "font-medium text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:text-accent";

/** True when Iron Jarvis has an installer job for this Launch-catalog CLI id. */
export function hasInstallJob(cli: string | undefined): boolean {
  return Boolean(cli && INSTALL_JOBS[cli]);
}

/** A not-installed Claude Code / Codex row: one press installs it in view,
 *  then "I've installed it — check now" looks again (POST /providers/rescan). */
export function CliInstallPress({
  provider,
  cli,
  name,
  installUrl,
  installUrlLabel,
}: {
  /** The /health provider id ("claude-cli" | "codex-cli"). */
  provider: string;
  /** The Launch-catalog id ("claude" | "codex"). */
  cli: string;
  name: string;
  installUrl?: string;
  installUrlLabel?: string;
}) {
  const { refresh } = useDaemon();
  const { install, start } = useCliInstall();
  const [checking, setChecking] = useState(false);
  const [still, setStill] = useState(false);
  const state = install[cli] ?? {};
  const launched = state.paneId !== undefined;

  async function check() {
    setChecking(true);
    setStill(false);
    try {
      // The doors' own reading: the rescan lists a subscription CLI only
      // when its program is on this PC (ConnectDoors' `rescan(c, "installed")`).
      const res = await post<{ detected?: Array<{ provider?: string }> }>("/providers/rescan");
      refresh();
      setStill(!(res?.detected ?? []).some((r) => r?.provider === provider));
    } catch {
      refresh();
    } finally {
      setChecking(false);
    }
  }

  return (
    <div data-testid={`cli-get-${cli}-install`} className="mt-1 text-[11px] leading-relaxed text-zinc-500">
      {launched ? (
        <span data-testid={`cli-install-pane-${cli}`} className="block text-zinc-400">
          {installingLine(name)}{" "}
          <Link href={paneHref(state.paneId)} className={ROW_LINK}>
            Open the Build terminal
          </Link>
          <span className="mt-1 flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid={`cli-installed-check-${cli}`}
              onClick={() => void check()}
              disabled={checking}
              className={`-ml-1 ${CALM_ACTION}`}
            >
              {checking ? (
                <Loader2 size={13} className="animate-spin motion-reduce:animate-none" aria-hidden />
              ) : (
                <RefreshCw size={13} aria-hidden />
              )}
              {INSTALLED_CHECK_LABEL}
            </button>
          </span>
          {still && (
            <span data-testid={`cli-still-missing-${cli}`} className="mt-0.5 block text-tone-warn">
              {stillNotInstalledLine(name)}
            </span>
          )}
        </span>
      ) : (
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          Not on this PC yet.
          <button
            type="button"
            data-testid={`cli-install-${cli}`}
            onClick={() => void start(cli)}
            disabled={state.busy}
            className={`-ml-1 ${CALM_ACTION}`}
          >
            {state.busy ? (
              <Loader2 size={13} className="animate-spin motion-reduce:animate-none" aria-hidden />
            ) : (
              <TerminalSquare size={13} aria-hidden />
            )}
            {installLabel(name)}
          </button>
          {installUrl && (
            <a href={installUrl} target="_blank" rel="noreferrer" className={ROW_LINK}>
              {installUrlLabel || "Read the vendor's install page"}
            </a>
          )}
        </span>
      )}
      {state.error && (
        <span role="alert" className="mt-0.5 block text-tone-danger">
          {state.error}
        </span>
      )}
    </div>
  );
}

/** Ollama answers on this PC but has no model: the doors' starter download. */
export function OllamaStarterPress() {
  const starter = useStarterModel();
  return (
    <div data-testid="ollama-no-model-row" className="mt-1 text-[11px] leading-relaxed text-zinc-500">
      Ollama is running but has no model yet.{" "}
      {starter.paneId !== undefined ? (
        <span data-testid="ollama-starter-pane" className="text-zinc-400">
          A Build terminal opened and is downloading {STARTER_MODEL_LABEL}. When it says success, press Look
          again.{" "}
          <Link href={paneHref(starter.paneId)} className={ROW_LINK}>
            Open the Build terminal
          </Link>
        </span>
      ) : (
        <button
          type="button"
          data-testid="ollama-starter-download"
          onClick={() => void starter.start()}
          disabled={starter.busy}
          className={`-ml-1 ${CALM_ACTION}`}
        >
          {starter.busy ? (
            <Loader2 size={13} className="animate-spin motion-reduce:animate-none" aria-hidden />
          ) : (
            <TerminalSquare size={13} aria-hidden />
          )}
          Download a starter model ({STARTER_MODEL_LABEL}, about 2 GB)
        </button>
      )}
      {starter.error && (
        <span role="alert" className="mt-0.5 block text-tone-danger">
          {starter.error}
        </span>
      )}
    </div>
  );
}
