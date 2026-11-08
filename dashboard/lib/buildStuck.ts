/**
 * v1.450.0 (Wave D, item 40): Build never waits in silence.
 *
 * Three states could say "still working…" for ever:
 *   - the Launch menu's "Detecting…" when the read of the installed coding
 *     tools failed or never came back (the page swallowed the failure);
 *   - "No terminals yet" with a raw error printed beside it when the list of
 *     terminals could not be read;
 *   - Build › Code's "Reading <tool>'s model list…" once its few re-reads
 *     (TOOLS_REREAD_TRIES) ran out with no list.
 *
 * Each now ends, after a bound, in "couldn't check" with a Try again that
 * asks again. Pure words and bounds; no imports.
 */

/** How long the Launch menu waits for the installed tools before it says it
 *  could not check (a cold read measured ~3 s; this is a hang detector). */
export const DETECT_BOUND_MS = 20_000;

/** What the Launch menu's state is. */
export type DetectState = "detecting" | "ready" | "failed";

export const DETECTING_WORDS = "Detecting…";

export const DETECT_FAILED_WORDS = "Couldn't check which coding tools are installed.";

export const NONE_FOUND_WORDS = "No coding tools found on this PC.";

/** The thing a failed read of the terminal list names. */
export const TERMINALS_THING = "Your terminals";

/** Build › Code: the model list never arrived after its re-reads. */
export function modelsUnreadWords(tool: string): string {
  return `Couldn't read ${tool}'s model list.`;
}

/** Build › Code: still reading (unchanged words). */
export function modelsReadingWords(tool: string): string {
  return `Reading ${tool}'s model list…`;
}

export const TRY_AGAIN_WORDS = "Try again";
