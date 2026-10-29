/**
 * THE NAMES OF THE CODING SURFACES (harness gaps, fix 4; moved v1.406.0).
 *
 * v1.389.0: Build's second view — where Jarvis writes, runs and checks code
 * in a folder, one conversation per session — was labelled "IJ harness", the
 * engine room's own word. The user's word for one session of it is **a
 * coding session** (confirmed 2026-10-25).
 *
 * v1.406.0 (part 1, the user's decision 2026-10-29): the coding sessions
 * moved out of Build into the CHAT page, behind a switch at its top that
 * reads **Chat | Code**. Build is the terminals only (for the user's own
 * coding tools — Claude Code, Codex, any CLI). So:
 *  - the switch's two words are CHAT_VIEW_NAME and CODE_VIEW_NAME;
 *  - the view is CODING_VIEW_NAME ("Code" — Help's glossary, Ctrl K);
 *  - its list of sessions is CODING_SESSIONS_LIST ("Coding sessions": the
 *    sidebar's heading over it and the list's accessible name);
 *  - one session is CODING_SESSION_LABEL / CODING_SESSION.
 *
 * EVERY surface that names the view or one of its sessions reads it from
 * here, so a different choice is a change to these lines. Internal names stay
 * as they are (ids, routes, `build:<id>` session ids, test ids, the daemon's
 * `harness/` package, "IJ harness" in code comments): VOCABULARY.md's
 * three-layer rule — the wire is never renamed for vocabulary. The old words
 * stay searchable as aliases in lib/nav.ts.
 *
 * Import-free on purpose: the bell and the sidebar are on every page, and
 * dozens of test files mock lib/api wholesale.
 */

/** The chat page switch's first word. */
export const CHAT_VIEW_NAME = "Chat";

/** The chat page switch's second word — the coding sessions' view. */
export const CODE_VIEW_NAME = "Code";

/** The view's name — Help's glossary, Ctrl K. */
export const CODING_VIEW_NAME = CODE_VIEW_NAME;

/** The list of coding sessions: the sidebar's heading over it in the Code
 *  view, and the list's accessible name. */
export const CODING_SESSIONS_LIST = "Coding sessions";

/** One session of the view, at the start of a line ("Coding session · 3 min"). */
export const CODING_SESSION_LABEL = "Coding session";

/** One session of the view, mid-sentence ("a coding session"). */
export const CODING_SESSION = CODING_SESSION_LABEL.toLowerCase();

/** Where the coding sessions live, mid-sentence ("in Chat › Code"). */
export const CODING_SESSIONS_PLACE = `${CHAT_VIEW_NAME} › ${CODE_VIEW_NAME}`;

/** Build in one plain line — the page's blurb in Ctrl K and Everything, and
 *  Help's "Build" entry. v1.406.0: the terminals only. */
export const BUILD_BLURB =
  "Terminals for your own coding tools — Claude Code, Codex or any command line — in your project folders.";

/** Chat in one plain line (v1.406.0): its Code switch is where the coding
 *  sessions are. */
export const CHAT_BLURB = `Talk to Iron Jarvis — quick answers or real work. Its ${CODE_VIEW_NAME} switch holds your ${CODING_SESSION}s.`;

/** The Code view in one plain line (Ctrl K's row for it). */
export const CODE_BLURB = `${CODING_SESSIONS_LIST}: Jarvis writes, runs and checks code in a folder you pick.`;
