"use client";

/**
 * Download a model for me, without a terminal (v1.355.0, G73; next leaps
 * 43-a).
 *
 * The free-and-private door's one press downloads the starter model INSIDE
 * the app, through the model library's own download (`POST
 * /fleet/nodes/{id}/models/download`, the one call every download uses): its
 * size and the drive's free space are checked by Iron Jarvis first (a
 * download that would leave less than a tenth of the drive free is refused in
 * words), then the door shows its progress with Cancel. Before 43-a the press
 * opened a Build terminal running the download, with no size, progress or
 * Cancel in the app. The press is the consent (the door names the model and
 * its size); nothing here types a command.
 *
 * `STARTER_MODEL` is pinned equal to the daemon's `terminals/ai_clis.
 * STARTER_MODEL` by tests/test_model_doors_v13550.py.
 *
 * Its own file, not lib/api.ts, because ~70 test files mock that module.
 */

import { useEffect, useState } from "react";
import {
  DOWNLOAD_EVENT_TYPES,
  foldDownload,
  readLibrary,
  startDownload,
  type Download,
} from "@/lib/modelLibrary";
import { useEvents } from "@/lib/useEvents";

/** The model it downloads (terminals/ai_clis.STARTER_MODEL). */
export const STARTER_MODEL = "llama3.2";
/** How the door names it. */
export const STARTER_MODEL_LABEL = "Llama 3.2";
/** Said when there is no Ollama on this PC to download into. */
export const NO_OLLAMA_HERE = "Ollama is not on this PC yet. Install it (step 1), then press Download again.";

/** The row as a door shows it: the door's own next step (step 3's "Use
 *  Ollama for answers", or Look again then "Use Ollama on this PC") is what
 *  puts the model in the model menu, so the daemon's general step
 *  ("Connect Ollama on Connections ...") is not said beside it. */
export function doorRow(d: Download): Download {
  return d.menu_step ? { ...d, menu_step: "" } : d;
}

/** Start the download into Ollama on this PC. Resolves the download's row;
 *  rejects with the daemon's own sentence. */
export async function downloadStarterModel(): Promise<Download> {
  const lib = await readLibrary("");
  const server = lib.servers.find((s) => s.kind === "ollama" && s.this_pc && s.downloads);
  if (!server) throw new Error(NO_OLLAMA_HERE);
  const d = await startDownload(server.id, STARTER_MODEL);
  if (!d) throw new Error("The download did not start. Press Download again.");
  return d;
}

/** The press, as a hook: in flight, the download it started (`null` = not
 *  pressed yet) with its live progress, or the refusal sentence. */
export function useStarterModel(onStarted?: () => void) {
  const [busy, setBusy] = useState(false);
  const [download, setDownload] = useState<Download | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { events } = useEvents(20, { types: DOWNLOAD_EVENT_TYPES });
  const id = download?.id ?? "";

  useEffect(() => {
    if (!id) return;
    const mine = events.filter((e) => e.payload?.id === id);
    if (!mine.length) return;
    setDownload((cur) => {
      if (!cur) return cur;
      const folded = mine.slice().reverse().reduce((acc, e) => foldDownload(acc, e.payload), [cur]);
      return folded.find((d) => d.id === cur.id) ?? cur;
    });
  }, [events, id]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      setDownload(await downloadStarterModel());
      onStarted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  /** The row the daemon answered a Cancel with. */
  function update(next: Download) {
    setDownload(next);
  }

  /** A stopped or failed download: the door shows its press again. */
  function again() {
    setDownload(null);
  }

  return { busy, download, error, start, update, again };
}
