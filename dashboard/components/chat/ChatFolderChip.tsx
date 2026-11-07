"use client";

/**
 * The chat toolbar's FOLDER chip (v1.443.0) — beside the project chip, once a
 * conversation has a message (a new chat shows Build › Code's full FolderChip
 * above the card instead). A folder icon, the folder's name when one is
 * chosen, and a press that opens the SAME picker Build › Code uses (the page
 * owns it: `useFolderPicker` from components/folders/FolderChip). Its name and
 * its × give way with the project's fit step (lib/toolbarFit), so the toolbar
 * stays one row; the "+" menu also clears the folder.
 */

import { Folder, FolderOpen, X } from "lucide-react";
import { composerChipClass, fitIconChip, fitWords } from "@/lib/composerChips";
import { folderLabel, type FolderPicker } from "@/components/folders/FolderChip";
import { CHOOSE_CHAT_FOLDER_TITLE, CLEAR_CHAT_FOLDER_TITLE } from "@/lib/chatFolder";

export function ChatFolderChip({
  folder,
  picker,
  title,
  problem = null,
  onClear,
}: {
  folder: string | null;
  picker: FolderPicker;
  /** The chip's hover (lib/chatFolder `chatFolderTitle`). */
  title: string;
  /** The daemon's words about the chosen folder (said on the card too). */
  problem?: string | null;
  /** Back to no folder (absent: no ×, e.g. the project's own folder). */
  onClear?: () => void;
}) {
  return (
    <span className="inline-flex shrink-0 items-center">
      <button
        type="button"
        data-testid="chat-folder-chip"
        data-folder={folder ?? ""}
        data-problem={problem ? "true" : undefined}
        aria-label={folder ? `Choose another folder (now ${folderLabel(folder)})` : CHOOSE_CHAT_FOLDER_TITLE}
        title={problem ? `${problem} ${title}` : title}
        disabled={picker.asking}
        onClick={() => void picker.choose()}
        className={`${composerChipClass(Boolean(folder))} ${fitIconChip("project")} ${
          problem ? "!text-tone-danger" : ""
        }`}
      >
        {folder ? <FolderOpen size={14} className="shrink-0" /> : <Folder size={14} className="shrink-0" />}
        {folder && (
          <span className={`hidden max-w-[9rem] truncate sm:inline ${fitWords("project")}`}>
            {folderLabel(folder)}
          </span>
        )}
      </button>
      {folder && onClear ? (
        <button
          type="button"
          data-testid="chat-folder-chip-clear"
          aria-label="No folder for this chat"
          title={CLEAR_CHAT_FOLDER_TITLE}
          onClick={onClear}
          className={`hidden h-[30px] w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 sm:grid ${fitWords("project")}`}
        >
          <X size={12} aria-hidden="true" />
        </button>
      ) : null}
    </span>
  );
}

export default ChatFolderChip;
