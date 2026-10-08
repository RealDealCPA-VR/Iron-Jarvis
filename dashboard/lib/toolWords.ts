/**
 * v1.314.0 (UX wave 2, approval-card-tool-ids): what a tool DOES, in the few
 * plain words a busy person can decide on — "save a file", not `write_file`.
 *
 * Used where the app asks for consent (the mid-turn approval card), so the
 * words must stay TRUE to the tool: each entry describes only what that tool
 * id does. An id this map does not know returns null and the caller falls
 * back to the exact id — never an invented description. The exact id always
 * stays on the card too (a <code> chip, the aria-label): it is the record of
 * what will run, and what a "this conversation" grant is keyed on.
 *
 * Phrases complete "The assistant wants to … — your call."
 */

const WORDS: Record<string, string> = {
  write_file: "save a file",
  edit_file: "change a file",
  // rename_file renames OR moves one file (tools/builtins.py: a `new_path`
  // that is a path puts it in another folder) — the consent lead must stay
  // true for a move too (v1.314.0 review).
  rename_file: "rename or move a file",
  // Built-ins ONLY. An agent-authored custom tool (tools/dynamic.py — e.g. a
  // `rename_real_file` CommandTool) runs whatever argv template its author
  // wrote, so the app cannot vouch for it in its own words: it falls back to
  // "run <its id>" on purpose (v1.314.0 review).
  shell: "run a command",
  repl: "run some code",
  run_code: "run some code",
  // web_fetch fetches a page and hands its TEXT back (tools/webfetch.py) —
  // nothing opens on the user's screen, so "read", not "open".
  web_fetch: "read a web page",
  web_search: "search the web",
};

// The 14 built-in browser tools, by EXACT name (src/iron_jarvis/browser/
// tools.py). Never a `browser_` prefix: an agent may create a custom
// CommandTool under any name the built-ins do not already use
// (tools/dynamic.py), so `browser_backup` would be a command of its author's
// choosing — described as "act in a browser tab" on a consent card, that is
// a lie (v1.314.0 review). An unknown name falls back to "run <its id>".
const BROWSER_TOOLS: ReadonlySet<string> = new Set([
  "browser_get_status",
  "browser_list_tabs",
  "browser_get_active_tab",
  "browser_read_page",
  "browser_get_elements",
  "browser_screenshot",
  "browser_activate_tab",
  "browser_scroll",
  "browser_create_tab",
  "browser_close_tab",
  "browser_click",
  "browser_type",
  "browser_press_key",
  "browser_navigate",
]);

/** v1.315.0: true only for the 14 BUILT-IN browser tools, by exact name —
 *  never a `browser_` prefix and never trimmed or case-folded (`Browser_click`
 *  is not one). The approval card offers "Allow for this tab" only for these:
 *  the daemon grants tabs to the real acting browser tools alone, so the
 *  button on any other tool would promise something that cannot happen. */
export function isBuiltinBrowserTool(tool: string): boolean {
  return typeof tool === "string" && BROWSER_TOOLS.has(tool);
}

/** Plain words for a tool id, or null when this map does not know it. */
export function toolWords(tool: string): string | null {
  const id = (tool || "").trim();
  if (!id) return null;
  if (Object.prototype.hasOwnProperty.call(WORDS, id)) return WORDS[id];
  // The built-in browser tools act in a tab of the browser the user
  // connected; the card's tab answer and note say the rest.
  if (isBuiltinBrowserTool(id)) return "act in a browser tab";
  return null;
}
