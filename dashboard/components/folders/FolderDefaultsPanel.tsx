"use client";

/**
 * v1.426.0 (Wave 6 lane B, E5): a folder's defaults for NEW coding sessions,
 * from the folder's heading in Chat › Code and Build › Code
 * (lib/folderDefaults; the daemon's harness/folder_defaults).
 *
 * The heading's sliders button opens this small panel under the heading: the
 * defaults in words (or that there are none), "Use for new sessions in this
 * folder" — the settings of the session open in front, when it is one of this
 * folder's — and Clear. The start composers apply what is saved here.
 *
 * `useFolderDefaults` is the composers' one read (lib/folderDefaults' cache:
 * a folder is read once).
 */

import { useEffect, useState } from "react";
import { Loader2, SlidersHorizontal } from "lucide-react";
import {
  NO_FOLDER_DEFAULTS_LINE,
  SAVE_DEFAULTS_LABEL,
  cachedFolderDefaults,
  folderBaseName,
  readFolderDefaults,
  saveFolderDefaults,
  usingDefaultsLine,
  type CodeDefaults,
  type FolderDefaults,
  type ToolDefaults,
} from "@/lib/folderDefaults";

/** The folder's defaults (null while there is no folder or the read is out).
 *  `version` bumps after a save made elsewhere so the reader reads again. */
export function useFolderDefaults(folder: string | null): FolderDefaults | null {
  const [got, setGot] = useState<FolderDefaults | null>(() => (folder ? cachedFolderDefaults(folder) ?? null : null));
  useEffect(() => {
    let live = true;
    if (!folder) {
      setGot(null);
      return;
    }
    setGot(cachedFolderDefaults(folder) ?? null);
    void readFolderDefaults(folder).then((d) => {
      if (live) setGot(d);
    });
    return () => {
      live = false;
    };
  }, [folder]);
  return got;
}

const PRESS =
  "rounded-md px-1.5 py-0.5 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-50";

/** The heading's button that opens the panel. */
export function FolderDefaultsButton({
  name,
  open,
  onToggle,
  testId,
  className = "",
}: {
  name: string;
  open: boolean;
  onToggle: () => void;
  testId: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-expanded={open}
      aria-label={`Defaults for new sessions in ${name}`}
      title={`Defaults for new sessions in ${name}`}
      onClick={onToggle}
      className={`grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${className}`}
    >
      <SlidersHorizontal size={11} aria-hidden="true" />
    </button>
  );
}

export function FolderDefaultsPanel<V extends "code" | "tool">({
  folder,
  view,
  describe,
  current,
  testId,
}: {
  folder: string;
  view: V;
  /** The defaults in words (the caller knows the model and tool names). */
  describe: (d: V extends "code" ? CodeDefaults : ToolDefaults) => string;
  /** The settings of the session open in front, when it is one of this
   *  folder's (null: none to offer). */
  current: (V extends "code" ? CodeDefaults : ToolDefaults) | null;
  testId: string;
}) {
  const defaults = useFolderDefaults(folder);
  const [saved, setSaved] = useState<FolderDefaults | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const shown = saved ?? defaults;
  const mine = shown ? (view === "code" ? shown.code : shown.tool) : null;
  const words = mine ? describe(mine as never) : "";
  const currentWords = current ? describe(current as never) : "";

  async function save(value: CodeDefaults | ToolDefaults | null) {
    setBusy(true);
    setProblem(null);
    try {
      setSaved(await saveFolderDefaults(folder, view, value));
    } catch (e) {
      setProblem(e instanceof Error && e.message ? e.message : "The defaults were not saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid={testId} className="mx-1 mb-1 space-y-1 rounded-lg bg-white/[0.03] px-2 py-1.5 text-[12px] text-zinc-400">
      {!shown ? (
        <p className="flex items-center gap-1.5 text-zinc-500">
          <Loader2 size={11} className="animate-spin" aria-hidden /> Reading…
        </p>
      ) : (
        <p data-testid={`${testId}-words`}>{words ? usingDefaultsLine(folderBaseName(folder), words) : NO_FOLDER_DEFAULTS_LINE}</p>
      )}
      <div className="flex flex-wrap items-center gap-1">
        {current && currentWords && currentWords !== words ? (
          <button
            type="button"
            data-testid={`${testId}-use`}
            disabled={busy}
            title={`New sessions here will start with: ${currentWords}`}
            onClick={() => void save(current)}
            className={PRESS}
          >
            {SAVE_DEFAULTS_LABEL}: {currentWords}
          </button>
        ) : null}
        {mine ? (
          <button type="button" data-testid={`${testId}-clear`} disabled={busy} onClick={() => void save(null)} className={PRESS}>
            Clear
          </button>
        ) : null}
        {!current && !mine && shown ? (
          <span className="text-zinc-500">Open a session in this folder, or start one, to set them.</span>
        ) : null}
      </div>
      {problem ? (
        <p role="alert" data-testid={`${testId}-problem`} className="text-tone-danger">
          {problem}
        </p>
      ) : null}
    </div>
  );
}
