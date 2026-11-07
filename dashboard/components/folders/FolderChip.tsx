"use client";

/**
 * THE FOLDER CHIP (v1.412.0) — "No folder" until one is chosen, then the
 * folder's name (its full path is the title), and "Choose…".
 *
 * The user: "when i decide i want to choose a folder a popup modal that is the
 * generic windows file selector should appear allowing me to navigate all the
 * drives available to me". In the desktop app, Choose… opens Windows' own
 * folder picker (desktop/main.js `dialog:pickFolder`, through
 * lib/desktopShell `folderPickerBridge`); in a browser (no bridge) it opens
 * the app's own folder tree in a dialog (components/terminal/DirectoryTree,
 * the tree the old New session forms used). Either way the path is only a
 * CHOICE: the daemon's own folder checks decide when the session is made, and
 * a refusal comes back here as `problem` — said under the chip, in its words.
 *
 * Used by Build's Code view (components/buildCode/StartComposer), the
 * coding sessions' composer (components/harness/HarnessSession) and, since
 * v1.443.0, the chat's own folder chip (the chat page calls the picking half,
 * `useFolderPicker`, once and hands it to every way in: the hero chip, the
 * toolbar chip, "+ Choose a working folder" and the drawer's Change), so there
 * is ONE picker, never a second copy.
 */

import { useRef, useState, type ReactNode } from "react";
import { FolderOpen, FolderX, X } from "lucide-react";
import { Modal } from "@/components/Modal";
import dynamic from "next/dynamic";
import { folderPickerBridge } from "@/lib/desktopShell";

/** The app's folder tree, loaded only when the dialog opens (v1.443.0): the
 *  chat page draws this chip, and the tree stays off the /chat route
 *  (chat-wave3-deferred-v1311 pins that), as it is in the chat's drawer. */
const DirectoryTree = dynamic(
  () => import("@/components/terminal/DirectoryTree").then((m) => ({ default: m.DirectoryTree })),
  { ssr: false },
);

/** The chip's words with no folder chosen. */
export const NO_FOLDER_WORDS = "No folder";
/** The press that opens the picker. */
export const CHOOSE_FOLDER_WORDS = "Choose…";

/** A folder's last part ("C:\\work\\shop" → "shop"; "D:\\" → "D:"). */
export function folderLabel(path: string): string {
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

const CHIP =
  "inline-flex h-7 max-w-[14rem] items-center gap-1.5 rounded-lg px-2 text-[12px] text-zinc-400";
const PRESS =
  "inline-flex h-7 items-center rounded-lg px-2 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";

/** The picking half of the chip: what Choose… does, and the in-app tree's
 *  dialog for when there is no Windows picker. `dialog` must be rendered
 *  (it is null while closed). */
export interface FolderPicker {
  /** Open Windows' own folder picker (the desktop app), else the tree. */
  choose: () => Promise<void>;
  /** Windows' picker is open. */
  asking: boolean;
  /** The in-app tree's dialog, or null while closed. */
  dialog: ReactNode;
}

/**
 * THE ONE PICKER (v1.443.0, lifted out of FolderChip unchanged). The desktop
 * app: Windows' own folder picker through `folderPickerBridge`, opened at the
 * chosen folder else `startAt`; a cancel picks nothing. No bridge (a browser),
 * or a picker that could not open: the app's folder tree in a dialog.
 */
export function useFolderPicker({
  folder,
  onPick,
  disabled = false,
  startAt = null,
  testId = "folder-chip",
}: {
  folder: string | null;
  onPick: (path: string) => void;
  disabled?: boolean;
  startAt?: string | null;
  testId?: string;
}): FolderPicker {
  const [treeOpen, setTreeOpen] = useState(false);
  const [treePick, setTreePick] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  // A press reads the newest handler, never the one of the render it began in.
  const pickRef = useRef(onPick);
  pickRef.current = onPick;
  const askingRef = useRef(false);

  async function choose() {
    if (disabled || askingRef.current) return;
    const bridge = folderPickerBridge();
    if (!bridge) {
      setTreePick(folder);
      setTreeOpen(true);
      return;
    }
    askingRef.current = true;
    setAsking(true);
    try {
      const got = await bridge(folder || startAt || "");
      if ("path" in got && got.path) pickRef.current(got.path);
    } catch {
      // The dialog could not open: the in-app tree is the way instead.
      setTreePick(folder);
      setTreeOpen(true);
    } finally {
      askingRef.current = false;
      setAsking(false);
    }
  }

  const dialog = treeOpen ? (
    <Modal label="Choose a folder" onClose={() => setTreeOpen(false)} testId={`${testId}-tree`} className="w-full max-w-xl">
      <div className="space-y-3 p-4">
        <p className="text-[14px] text-zinc-100">Choose a folder</p>
        <p className="truncate text-[12px] text-zinc-500" title={treePick ?? undefined}>
          {treePick ? treePick : "Pick a folder below, or type its path."}
        </p>
        <div className="max-h-[50vh] overflow-y-auto rounded-xl bg-ink-900/40 p-1">
          <DirectoryTree
            variant="calm"
            hideAction
            hideHeaderCollapse
            showProjects
            pickTypedPath
            selectedPath={treePick}
            onSelect={setTreePick}
          />
        </div>
        <div className="flex items-center justify-end gap-2">
          <button type="button" onClick={() => setTreeOpen(false)} className={PRESS}>
            Cancel
          </button>
          <button
            type="button"
            data-testid={`${testId}-tree-use`}
            disabled={!treePick}
            onClick={() => {
              if (!treePick) return;
              setTreeOpen(false);
              pickRef.current(treePick);
            }}
            className="inline-flex h-8 items-center rounded-lg bg-accent px-3 text-[12px] font-medium text-ink-950 disabled:opacity-40"
          >
            Use this folder
          </button>
        </div>
      </div>
    </Modal>
  ) : null;

  return { choose, asking, dialog };
}

export function FolderChip({
  folder,
  onPick,
  onClear,
  problem = null,
  disabled = false,
  startAt = null,
  noFolderTitle = "No folder: the session gets a folder of its own with your first message.",
  testId = "folder-chip",
  picker,
  clearTitle = "Start with no folder instead",
  chooseTitle = "Choose the folder to work in",
}: {
  /** The chosen folder, or null for none. */
  folder: string | null;
  /** A folder was chosen (the caller sends it on; the daemon checks it). */
  onPick: (path: string) => void;
  /** Back to "No folder" (absent: no ×). */
  onClear?: () => void;
  /** The daemon refused the chosen folder: its words, said under the chip. */
  problem?: string | null;
  disabled?: boolean;
  /** Where the picker opens (the chosen folder wins). */
  startAt?: string | null;
  /** The chip's title with no folder (where the session's own folder goes). */
  noFolderTitle?: string;
  testId?: string;
  /** A picker the caller owns (it renders its dialog); absent = the chip's own. */
  picker?: FolderPicker;
  /** The ×'s title. */
  clearTitle?: string;
  /** Choose…'s title. */
  chooseTitle?: string;
}) {
  const own = useFolderPicker({ folder, onPick, disabled, startAt, testId });
  const { choose, asking } = picker ?? own;

  return (
    <span className="inline-flex min-w-0 flex-col">
      <span className="inline-flex min-w-0 items-center">
        <span
          data-testid={testId}
          data-folder={folder ?? ""}
          data-problem={problem ? "true" : undefined}
          title={problem ? problem : folder || noFolderTitle}
          className={`${CHIP} ${problem ? "text-tone-danger" : ""}`}
        >
          {folder ? (
            <FolderOpen size={13} aria-hidden="true" className="shrink-0" />
          ) : (
            <FolderX size={13} aria-hidden="true" className="shrink-0" />
          )}
          <span className="truncate">{folder ? folderLabel(folder) : NO_FOLDER_WORDS}</span>
        </span>
        {folder && onClear ? (
          <button
            type="button"
            data-testid={`${testId}-clear`}
            aria-label="No folder"
            title={clearTitle}
            disabled={disabled}
            onClick={onClear}
            className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 disabled:opacity-50"
          >
            <X size={12} aria-hidden="true" />
          </button>
        ) : null}
        <button
          type="button"
          data-testid={`${testId}-choose`}
          aria-label={folder ? `Choose another folder (now ${folderLabel(folder)})` : "Choose a folder"}
          title={chooseTitle}
          disabled={disabled || asking}
          onClick={() => void choose()}
          className={PRESS}
        >
          {CHOOSE_FOLDER_WORDS}
        </button>
      </span>
      {problem ? (
        <span role="alert" data-testid={`${testId}-problem`} className="max-w-[28rem] px-2 text-[12px] text-tone-danger">
          {problem}
        </span>
      ) : null}
      {picker ? null : own.dialog}
    </span>
  );
}

export default FolderChip;
