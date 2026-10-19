"use client";

/**
 * Download a model for me, without a terminal (v1.355.0, G73).
 *
 * The free-and-private door used to say "Pull a model — copy this into a
 * terminal: ollama pull llama3.2". Now one press opens a Build pane that runs
 * the download — `POST /terminals/launch {cli: "ollama-starter"}`, the SAME
 * door the Sign in press uses (lib/cliSignIn). The launch route accepts only
 * catalog ids, so this asks for a set-up job by NAME
 * (`terminals/ai_clis.SETUP_LAUNCHES`) and never sends a command: nothing
 * here can type an arbitrary line into a shell. The click is the consent (the
 * ResumeStrip rule), and the pane shows the download as it happens.
 *
 * `STARTER_MODEL_CLI` and `STARTER_MODEL` are pinned equal to the daemon's
 * catalog by tests/test_model_doors_v13550.py.
 *
 * Its own file, not lib/api.ts, because ~70 test files mock that module.
 */

import { useState } from "react";
import { post } from "@/lib/api";

/** The set-up job's catalog id (terminals/ai_clis.SETUP_LAUNCHES). */
export const STARTER_MODEL_CLI = "ollama-starter";
/** The model it downloads (terminals/ai_clis.STARTER_MODEL). */
export const STARTER_MODEL = "llama3.2";
/** How the door names it. */
export const STARTER_MODEL_LABEL = "Llama 3.2";

/** Open the download pane. Resolves the new pane's id (null when the daemon
 *  did not say); rejects with the daemon's own sentence. */
export async function launchStarterModel(): Promise<string | null> {
  const res = await post<{ id?: string }>("/terminals/launch", { cli: STARTER_MODEL_CLI });
  return res && typeof res.id === "string" ? res.id : null;
}

/** The press, as a hook: in flight, the pane it opened (`null` = opened, id
 *  unknown; `undefined` = not pressed yet), or the refusal sentence. */
export function useStarterModel(onLaunched?: () => void) {
  const [busy, setBusy] = useState(false);
  const [paneId, setPaneId] = useState<string | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      setPaneId(await launchStarterModel());
      onLaunched?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return { busy, paneId, error, start };
}
