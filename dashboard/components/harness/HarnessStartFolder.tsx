"use client";

/**
 * A coding session's FOLDER CHIP before its first message (v1.412.0).
 *
 * New session opens the composer at once, with NO folder (the v1.379.0
 * folderless session); this row under the composer says so and offers
 * "Choose…" — Windows' own folder picker in the desktop app, the app's folder
 * tree in a browser (components/folders/FolderChip). A session's folder is
 * fixed once it is made, so choosing one before the first message MOVES the
 * empty session: a new session in that folder (the one create path, POST
 * /build/sessions — its folder checks, its own branch and worktree) with the
 * same model, account, effort, approvals and mode, and the words in the box
 * carried over; the empty one goes. A folder the daemon refuses is said on
 * the chip and nothing moves.
 *
 * A folder in a git repository that can start a branch asks one thing first,
 * as New session's form did (v1.375.0): "Work on a new branch" (on by
 * default) — off works in the folder directly.
 */

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { FolderChip } from "@/components/folders/FolderChip";
import { fetchFolderGit, folderGitLine, type FolderGit } from "@/lib/buildGit";

export function HarnessStartFolder({
  folder,
  workRoot,
  startAt = null,
  disabled = false,
  onMove,
}: {
  /** The session's folder ("" / null = none yet). */
  folder: string | null;
  /** Where a session with no folder keeps its files. */
  workRoot: string;
  /** Where the picker opens when no folder is chosen. */
  startAt?: string | null;
  disabled?: boolean;
  /** Move the session to `folder` ("" = no folder); `branch` only when the
   *  switch was shown. Resolves to the daemon's refusal, or null. */
  onMove: (folder: string, branch?: boolean) => Promise<string | null>;
}) {
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState<{ folder: string; info: FolderGit } | null>(null);
  const [onBranch, setOnBranch] = useState(true);
  const [note, setNote] = useState<string | null>(null);

  async function move(path: string, branch?: boolean) {
    setBusy(true);
    setProblem(null);
    const err = await onMove(path, branch);
    setBusy(false);
    if (err) setProblem(err);
  }

  async function picked(path: string) {
    setProblem(null);
    setNote(null);
    setBusy(true);
    const info = await fetchFolderGit(path);
    setBusy(false);
    if (info && info.repo && info.canBranch) {
      setOnBranch(true);
      setAsking({ folder: path, info });
      return;
    }
    const line = folderGitLine(info);
    if (line) setNote(line);
    await move(path);
  }

  return (
    <div data-testid="harness-start-chips" className="flex min-w-0 flex-wrap items-start gap-1 px-2 pt-1.5">
      <FolderChip
        folder={folder || null}
        onPick={(p) => void picked(p)}
        onClear={folder ? () => void move("") : undefined}
        problem={problem}
        disabled={disabled || busy || !!asking}
        startAt={startAt}
        noFolderTitle={
          workRoot
            ? `No folder: the session keeps its files in a new folder in ${workRoot}, made with your first message.`
            : "No folder: the session keeps its files in a new folder, made with your first message."
        }
        testId="harness-start-folder"
      />
      {busy ? <Loader2 size={13} aria-label="Moving the session" className="mt-2 animate-spin text-zinc-500" /> : null}
      {note && !problem ? (
        <span data-testid="harness-start-folder-note" className="self-center px-1 text-[11px] text-zinc-500">
          {note}
        </span>
      ) : null}
      {asking ? (
        <div
          data-testid="harness-start-branch"
          className="flex w-full min-w-0 flex-wrap items-center gap-2 rounded-xl bg-white/[0.03] px-2.5 py-2 text-[12px] text-zinc-300"
        >
          <label className="flex min-w-0 flex-1 items-start gap-2">
            <input
              type="checkbox"
              data-testid="harness-start-branch-switch"
              checked={onBranch}
              onChange={(e) => setOnBranch(e.target.checked)}
              className="mt-0.5 accent-accent"
            />
            <span className="min-w-0">
              Work on a new branch
              <span className="block text-[11px] text-zinc-500">
                {onBranch
                  ? `A worktree beside the repository, on its own ij/ branch from ${asking.info.branch || "the current commit"}. Your checkout is not touched.`
                  : "Off: the session works in the folder directly, on the branch checked out there."}
              </span>
            </span>
          </label>
          <button
            type="button"
            data-testid="harness-start-branch-use"
            onClick={() => {
              const f = asking.folder;
              setAsking(null);
              void move(f, onBranch);
            }}
            className="btn-accent text-xs"
          >
            Use this folder
          </button>
          <button
            type="button"
            onClick={() => setAsking(null)}
            className="rounded-md px-2 py-1 text-[12px] text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-100"
          >
            Cancel
          </button>
        </div>
      ) : null}
    </div>
  );
}

export default HarnessStartFolder;
