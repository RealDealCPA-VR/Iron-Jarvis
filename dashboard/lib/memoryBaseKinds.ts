/**
 * How a memory base's KIND is named on its row (v1.408.0). The Long-term
 * list showed the raw record value ("mcp", "http_rag", "google_drive"); the
 * daemon's `/ltm/sources` now reports each base's real kind, and this is the
 * one place that says it in words. An unknown kind reads as itself.
 */
export const MEMORY_BASE_KIND_WORDS: Readonly<Record<string, string>> = {
  markdown: "folder",
  notion: "Notion",
  ssh: "folder on another computer",
  google_drive: "Google Drive",
  onedrive: "OneDrive",
  dropbox: "Dropbox",
  http_rag: "search service",
  mcp: "memory server",
};

export function memoryBaseKindWords(kind: string): string {
  return MEMORY_BASE_KIND_WORDS[kind] ?? kind;
}

/** The second line of a memory server's row: where it is (its address),
 *  or that it runs on this PC (a command, never shown: its arguments may
 *  name private paths). */
export function memoryServerWhere(endpointUrl: unknown): string {
  const url = typeof endpointUrl === "string" ? endpointUrl.trim() : "";
  return url || "runs on this PC";
}
