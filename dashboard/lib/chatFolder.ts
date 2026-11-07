/**
 * THE CHAT'S FOLDER (v1.443.0). The user: "there is an option to select a
 * project, but what if i dont want it to be a specific project - rather just
 * work in a specific folder and that selection process should be exactly as
 * the folder is selected in build > code."
 *
 * The chat page picks with Build › Code's own picker
 * (components/folders/FolderChip `useFolderPicker`: Windows' folder picker in
 * the desktop app, the app's folder tree in a browser). This file holds the
 * rules and words around a pick, kept out of the 15,000-line page so they can
 * be read and tested on their own:
 *
 * - a folder INSIDE the active project (its root or a folder under it) keeps
 *   the chat in the project (the v1.329.0 "working in a folder of the project"
 *   case); a folder OUTSIDE it takes the chat out of the project;
 * - right after a pick the daemon is asked whether the chat can work there
 *   (`POST /documents/folder-check`, the same rule the chat lanes use), and a
 *   refusal is said under the chip. The pick stands either way.
 */

/** The route the chip asks after a pick. */
export const FOLDER_CHECK_PATH = "/documents/folder-check";

/** The toolbar chip's title with no folder. */
export const CHOOSE_CHAT_FOLDER_TITLE = "Choose a folder for this chat";
/** The new-chat chip's title with no folder. */
export const CHAT_NO_FOLDER_TITLE =
  "No folder chosen. A file you attach gets a folder of its own.";
/** The ×'s title. */
export const CLEAR_CHAT_FOLDER_TITLE = "Work with no folder in this chat";

function norm(p: string): string {
  return p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

/** `child` is `parent` or a folder under it (slashes either way, case
 *  ignored: Windows paths are case-blind). Never true for an empty path. */
export function insideFolder(child: string, parent: string): boolean {
  const c = norm(child.trim());
  const p = norm(parent.trim());
  if (!c || !p) return false;
  return c === p || c.startsWith(`${p}/`);
}

/** Does picking `path` take the chat out of the project whose folder is
 *  `projectRoot`? No project (or a project with no folder): never. */
export function pickLeavesProject(path: string, projectRoot: string | null | undefined): boolean {
  const root = (projectRoot ?? "").trim();
  if (!root) return false;
  return !insideFolder(path, root);
}

/** The toolbar chip's title: the full path (or the press), and in a project
 *  what a pick outside it does. */
export function chatFolderTitle(folder: string | null | undefined, projectName?: string | null): string {
  const f = (folder ?? "").trim();
  const base = f ? `Working in ${f}. Press to choose another folder.` : `${CHOOSE_CHAT_FOLDER_TITLE}.`;
  const name = (projectName ?? "").trim();
  return name ? `${base} Choosing a folder outside ${name} takes this chat out of it.` : base;
}

type Post = (path: string, body: unknown) => Promise<unknown>;

/**
 * Ask the daemon whether the chat can work in `path`. Its sentence, or null
 * (it can, it did not look — a network folder — or it could not answer: an
 * older daemon, offline; nothing is guessed).
 */
export async function checkChatFolder(path: string, post: Post): Promise<string | null> {
  try {
    const res = (await post(FOLDER_CHECK_PATH, { path })) as { problem?: unknown } | null | undefined;
    const p = res && typeof res.problem === "string" ? res.problem.trim() : "";
    return p || null;
  } catch {
    return null;
  }
}
