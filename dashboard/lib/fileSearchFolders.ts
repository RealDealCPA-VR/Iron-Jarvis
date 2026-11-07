/**
 * The folders File Search looks in (v1.443.0), said on the File Search page.
 *
 * The daemon's rule is `filesearch/defaults.search_folders`: with no folder
 * picked, a name or contents search looks in the Iron Jarvis chat files
 * folder, every active project's folder and the folders the user added
 * (`search_roots`); "By meaning" looks only in the added ones. Never the
 * app's own data folder (it used to: the old default was the install's
 * project_root). The page reads what the daemon already says — settings
 * values, projects, the drives list (which names "Documents") — and draws
 * that list here. Kept out of the page module: a Next page may export only
 * its component.
 */

export const DEFAULT_LABEL = "Your folders";

/** What "By meaning" needs to really match meaning (memory/embeddings.py). */
export const MEANING_NEEDS_WORDS =
  'By meaning needs a local embedding model to match meaning: install Ollama, run ' +
  '"ollama pull nomic-embed-text", connect Ollama in Settings › Connections, then ' +
  "restart Iron Jarvis. Without one it matches words.";

export const MEANING_ONLY_ADDED_WORDS =
  "By meaning looks only in the folders you add here (it reads every file in them once, " +
  "and chat may recall short passages from them).";

export const NO_MEANING_FOLDERS_WORDS = "Nothing to search by meaning yet: add a folder below.";

export const SAVED_WORDS = "Saved. The next search uses it.";

/** One folder the default search covers, in the order the daemon reads them. */
export type SearchPlace = {
  path: string;
  label: string;
  kind: "chat_files" | "project" | "added";
};

/** Compare two folder paths the way Windows does (case, slashes, a trailing one). */
export function samePath(a: string, b: string): boolean {
  const norm = (p: string) =>
    p.trim().replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase();
  return norm(a) === norm(b);
}

function lastPart(p: string): string {
  const parts = p.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

/** The folders the default search covers, in the daemon's order. */
export function searchPlaces(opts: {
  chatFilesRoot: string;
  documents: string | null;
  projects: { name: string; root: string }[];
  added: string[];
}): SearchPlace[] {
  const chat =
    opts.chatFilesRoot.trim() ||
    (opts.documents
      ? `${opts.documents.replace(/[\\/]+$/, "")}\\Iron Jarvis`
      : "Documents\\Iron Jarvis");
  const out: SearchPlace[] = [{ path: chat, label: "Iron Jarvis chat files", kind: "chat_files" }];
  for (const p of opts.projects) out.push({ path: p.root, label: p.name, kind: "project" });
  for (const a of opts.added) {
    const label = opts.documents && samePath(a, opts.documents) ? "Documents" : lastPart(a);
    out.push({ path: a, label, kind: "added" });
  }
  return out;
}
