/**
 * THE NAME OF BUILD'S CODING-AGENT VIEW (harness gaps, fix 4).
 *
 * Build has two views. The second — where Jarvis writes, runs and checks
 * code in a folder, one conversation per session — was labelled "IJ harness",
 * the engine room's own word (the v1.388.0 re-audit, 0g.6: on the view switch,
 * its sidebar, the bell and two terminal refusals). The user's word for it is
 * **Coding sessions** (confirmed 2026-10-25): the switch reads
 * "Terminals | Coding sessions", and one session of it is "a coding session".
 *
 * EVERY surface that names the view or one of its sessions reads it from
 * here, so a different choice is a change to these two lines. Internal names
 * stay as they are (ids, routes, `ij.build.shape` values, test ids, the
 * daemon's `harness/` package, "IJ harness" in code comments): VOCABULARY.md's
 * three-layer rule — the wire is never renamed for vocabulary. The old words
 * stay searchable as aliases in lib/nav.ts.
 *
 * Import-free on purpose: the bell and the sidebar are on every page, and
 * dozens of test files mock lib/api wholesale.
 */

/** The view's name — the Build switch's second choice, Help's glossary. */
export const CODING_VIEW_NAME = "Coding sessions";

/** One session of the view, at the start of a line ("Coding session · 3 min"). */
export const CODING_SESSION_LABEL = "Coding session";

/** One session of the view, mid-sentence ("a coding session"). */
export const CODING_SESSION = CODING_SESSION_LABEL.toLowerCase();

/** Build in one plain line — the page's blurb in Ctrl K and Everything, and
 *  Help's "Build" entry. Both views, never only the terminals. */
export const BUILD_BLURB = `Terminals for your own coding tools, and ${CODING_VIEW_NAME.toLowerCase()} where Jarvis writes and runs code in a folder.`;
