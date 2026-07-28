// The memory-base catalogue (v1.110.0), shared by the chooser and the setup
// walkthrough.
//
// Adding a base used to be a <select> whose first option was "Offsite RAG
// endpoint" — the most technical thing on the list, presented first, to someone
// who has not yet decided what a memory base IS. Every option was named after
// its transport rather than what the user has: "markdown" rather than "a folder
// of notes on this PC".
//
// So each entry answers the three questions someone actually has: what is this,
// do I have one, and what will it ask me for. Order is easiest-first — the
// local folder is the one most people can finish in ten seconds, and it is the
// one that should be staring at them.

export type BaseKind =
  | "markdown"
  | "notion"
  | "google_drive"
  | "onedrive"
  | "dropbox"
  | "ssh"
  | "mcp"
  | "http_rag";

export interface BaseOption {
  kind: BaseKind;
  /** What the user has, not how we talk to it. */
  label: string;
  /** One line: what lands in memory if they pick this. */
  blurb: string;
  /** Exactly what the form will ask for — no surprises mid-setup. */
  needs: string;
  /** Lucide icon name, resolved by the chooser. */
  icon:
    | "FolderOpen"
    | "NotebookPen"
    | "Cloud"
    | "Server"
    | "Boxes"
    | "Globe";
  /** Shown as a quiet chip. Honest about effort, so nobody starts the hard one first. */
  effort: "quickest" | "easy" | "needs a token" | "technical";
}

export const BASE_CATALOG: BaseOption[] = [
  {
    kind: "markdown",
    label: "A folder on this PC",
    blurb:
      "Any folder of notes — including an Obsidian vault. Searched and added to in place.",
    needs: "the folder",
    icon: "FolderOpen",
    effort: "quickest",
  },
  {
    kind: "notion",
    label: "A Notion database",
    blurb: "Pages in one Notion database become searchable memory.",
    needs: "the database id and an integration token",
    icon: "NotebookPen",
    effort: "needs a token",
  },
  {
    kind: "google_drive",
    label: "Google Drive",
    blurb: "Notes and documents in a Drive folder.",
    needs: "your connected Google account",
    icon: "Cloud",
    effort: "easy",
  },
  {
    kind: "onedrive",
    label: "OneDrive",
    blurb: "Notes and documents in a OneDrive folder.",
    needs: "your connected Microsoft account",
    icon: "Cloud",
    effort: "easy",
  },
  {
    kind: "dropbox",
    label: "Dropbox",
    blurb: "Notes and documents in a Dropbox folder.",
    needs: "your connected Dropbox account",
    icon: "Cloud",
    effort: "easy",
  },
  {
    kind: "ssh",
    label: "A folder on another machine",
    blurb: "Notes on a server or another PC, reached over SSH.",
    needs: "host, folder, and a password or key",
    icon: "Server",
    effort: "technical",
  },
  {
    kind: "mcp",
    label: "An MCP memory server",
    blurb: "Paste a Claude-Desktop-style config and its memory becomes a base.",
    needs: "the server's config JSON",
    icon: "Boxes",
    effort: "technical",
  },
  {
    kind: "http_rag",
    label: "Your own search endpoint",
    blurb: "An HTTP API you host that answers search queries.",
    needs: "the endpoint URL (and a token, if it needs one)",
    icon: "Globe",
    effort: "technical",
  },
];

export const EFFORT_TONE: Record<BaseOption["effort"], string> = {
  quickest: "text-emerald-300/90",
  easy: "text-emerald-300/70",
  "needs a token": "text-amber-300/80",
  technical: "text-zinc-500",
};

export function baseOption(kind: string): BaseOption | undefined {
  return BASE_CATALOG.find((b) => b.kind === kind);
}
