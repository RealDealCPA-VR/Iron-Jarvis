/**
 * v1.351.0 (G08): what an add door says when the pack it saved did not start.
 *
 * Every Tools add door (paste, import from your other apps, HTTP server, the
 * catalog) used to announce a pack with ZERO tools in the success colour,
 * "added", with a green check, while the bell said it was failing. A pack
 * that loaded nothing is SAVED, not working, and the door now says so in the
 * warn tone with the daemon's own reason and the one next step:
 *
 *   "Added, but it didn't start: npx isn't installed. Open its row below to retry."
 *
 * The daemon words its add note as "saved, but it did not start: <reason> —
 * see its row for the fix" (routes/agents.add_mcp_pack); the wrapper words are
 * taken off so the reason is said once, in the door's own sentence. Plain data
 * (no React), so every door and its tests read the same words.
 */

/** True when an add answer left the pack with no tools: saved, not working. */
export function packDidNotStart(res: { tools_loaded?: number | null } | null | undefined): boolean {
  const n = res?.tools_loaded;
  return !(typeof n === "number" && n > 0);
}

/** The daemon's reason, without its "saved, but it did not start:" and
 *  "— see its row for the fix" wrapper; "" when there is none. */
export function packStartReason(note: string | null | undefined): string {
  let t = typeof note === "string" ? note.trim() : "";
  if (!t) return "";
  t = t.replace(/^saved,?\s+but\s+it\s+did\s*n[o']t\s+start\s*:\s*/i, "");
  t = t.replace(/^saved\s*[—–-]\s*/i, "");
  t = t.replace(/\s*[—–;-]\s*see\s+its\s+row(\s+for\s+the\s+fix)?\s*\.?\s*$/i, "");
  return t.replace(/[\s.]+$/, "").trim();
}

/**
 * The sentence: "Added, but it didn't start: <reason>. Open its row below to
 * retry." With a `subject` ('"my-server"', 'Extension "slack"') the sentence
 * names it: '"my-server" added, but it didn't start: …'.
 */
export function addedButNotStarted(note: string | null | undefined, subject?: string): string {
  const reason = packStartReason(note);
  const head = subject ? `${subject} added, but it didn't start` : "Added, but it didn't start";
  return `${head}${reason ? `: ${reason}` : ""}. Open its row below to retry.`;
}
