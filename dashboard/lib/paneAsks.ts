/**
 * A Build pane waiting on YOU, said where you are (v1.353.0, G52).
 *
 * Build already knows when the coding CLI in a pane has stopped on a question
 * ("Allow this edit? 1. Yes 2. No"): the classifier calls that state
 * `blocked`, the pane wears "needs you", and the canvas strip counts it. All
 * of that lives ON Build. A user who started Claude Code in a pane and went
 * back to chat never learned it was waiting — the pane sat there until they
 * happened to look.
 *
 * The bell now reads the same tiny `GET /terminals/activity` the Build page
 * polls and lists one row per blocked pane: "Claude Code in Build is waiting
 * for you", opening that pane (`/terminals?focus=<id>`, Build's own deep
 * link). The desktop's ask watcher (desktop/main.js `planPaneNotifications`)
 * reads the same listing with the same words for a closed window.
 *
 * Keyed ONLY on `blocked`: a pane that is working, idle or unreadable is not
 * waiting on anyone, and a row for it would be noise.
 *
 * Dependency-free on purpose: the bell is on every page and `lib/api` is
 * mocked wholesale by dozens of tests.
 */

/** The CLI's own name by its catalog id — the SAME labels the Launch menu
 *  shows (src/iron_jarvis/terminals/ai_clis.py `AI_CLIS`; the desktop's copy
 *  in main.js `PANE_CLI_NAMES`). Pinned equal to both by
 *  tests/test_pane_asks_v13530.py, so the three can never name one CLI two
 *  ways. */
export const PANE_CLI_NAMES: Readonly<Record<string, string>> = {
  claude: "Claude Code",
  codex: "Codex",
  grok: "Grok CLI",
  opencode: "opencode",
  pi: "Pi",
  gemini: "Gemini CLI",
  "cursor-agent": "Cursor Agent",
  aider: "Aider",
  crush: "Crush",
  goose: "Goose",
  qwen: "Qwen Code",
  llm: "llm",
  ollama: "Ollama",
};

/** One pane waiting on the user. */
export interface BlockedPane {
  id: string;
  /** The pane's own name, when the user (or an agent) gave it one. */
  name: string;
  /** The CLI's catalog id ("claude"), or "" when nothing names it. */
  cli: string;
}

/** What a waiting pane is called in a sentence: the CLI's own name, an id
 *  the catalog has never heard of as itself (a name we half-know beats
 *  none — Build's rail rule), else "A Build terminal" (v1.357.0: the canon's
 *  word; desktop/main.js says the same). */
export function paneWaitingSubject(cli: string): string {
  const id = cli.trim();
  if (!id) return "A Build terminal";
  return PANE_CLI_NAMES[id] ?? id;
}

/** The row's words: "Claude Code in Build is waiting for you". With no CLI
 *  named: "A Build terminal is waiting for you". */
export function paneWaitingTitle(p: Pick<BlockedPane, "cli">): string {
  const subject = paneWaitingSubject(p.cli);
  return p.cli.trim()
    ? `${subject} in Build is waiting for you`
    : `${subject} is waiting for you`;
}

/** Where the row opens: that pane, in Build (the page reads `?focus=` on
 *  load and brings the pane to the front). */
export function paneWaitingHref(p: Pick<BlockedPane, "id">): string {
  return `/terminals?focus=${encodeURIComponent(p.id)}`;
}

/** The panes in a `GET /terminals/activity` answer that are waiting on the
 *  user — state `blocked` and not known dead — in the daemon's order. Any
 *  other shape (an older daemon, a test's bare mock, an error body) is no
 *  rows, never a crash. */
export function blockedPanes(data: unknown): BlockedPane[] {
  const rows =
    data && typeof data === "object" && Array.isArray((data as { panes?: unknown }).panes)
      ? ((data as { panes: unknown[] }).panes)
      : [];
  const out: BlockedPane[] = [];
  const seen = new Set<string>();
  for (const raw of rows) {
    if (!raw || typeof raw !== "object") continue;
    const r = raw as Record<string, unknown>;
    if (r.state !== "blocked" || r.alive === false) continue;
    const id = typeof r.id === "string" ? r.id.trim() : "";
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push({
      id,
      name: typeof r.name === "string" ? r.name.trim() : "",
      cli: typeof r.agent_cli === "string" ? r.agent_cli.trim() : "",
    });
  }
  return out;
}

/** True on Build itself (and a Build pop-out), where every waiting pane
 *  already wears "needs you" and the canvas strip counts them: the bell
 *  stays quiet there, so driving Claude Code through ten edit prompts never
 *  rings the badge or pings the desktop. */
export function onBuildPage(pathname: string | null | undefined): boolean {
  const p = (pathname ?? "").trim();
  return p === "/terminals" || p.startsWith("/terminals/") || p.startsWith("/terminals?");
}
