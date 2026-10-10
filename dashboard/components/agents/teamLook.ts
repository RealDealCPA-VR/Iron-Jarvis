/**
 * The calm look of the /agents "Your team" tab (v1.329.0, calm chat wave 9,
 * K4), in one place so the panel, the inbox, the folder, the coach and the
 * face picker draw the same quiet parts.
 *
 * The New task view beside this tab set the rules: no boxed cards, a quiet
 * sentence-case label over each section with a hairline setting it apart,
 * ghost controls that fill on hover, and ONE clear primary per section (quiet
 * until there is something to do, then the accent). Tone tokens only and
 * whole-pixel sizes.
 *
 * Plain strings with no imports, so the project page (which draws the inbox's
 * AssignmentRow) pulls in nothing more than these words.
 */

/** A section's label: quiet, sentence case, never shouted. */
export const SECTION_LABEL = "text-[13px] font-medium text-zinc-400";

/** A sub-label inside a section (Running, Shape, History…). */
export const SUB_LABEL = "text-[12px] font-medium text-zinc-500";

/** A ghost control's shape with no hover ink, for a caller that hovers into
 *  a tone (a Remove that turns the danger tone). */
export const GHOST_BASE =
  "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] font-medium text-zinc-400 transition-colors hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50";

/** A ghost control: no border, no fill at rest, fills on hover, a visible
 *  ring for a keyboard user. */
export const GHOST_BTN = `${GHOST_BASE} hover:text-zinc-100`;

/** A small ghost for a row's actions (View, Restore, Cancel…). A tone class
 *  is added beside it when the action carries a meaning. */
export const ROW_BTN =
  "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium transition-colors hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50";

/** The one primary of a section, sized like the mission's Start: the accent
 *  when `ready`, a quiet filled pill when there is nothing to do yet. */
export function primaryBtn(ready: boolean): string {
  return `inline-flex h-[32px] shrink-0 items-center gap-1.5 rounded-full px-3.5 text-[13px] ${
    ready ? "btn-accent" : "cursor-not-allowed bg-white/[0.06] text-zinc-500"
  }`;
}

/** A borderless field inside a calm section: a faint fill and no border, so
 *  a form reads as text to fill in rather than a stack of boxes. Focus shows
 *  as a thin accent ring. */
export const QUIET_FIELD =
  "w-full rounded-md bg-white/[0.03] px-2.5 py-1.5 text-[13px] text-zinc-100 outline-none transition-colors placeholder:text-zinc-500 focus:bg-white/[0.05] focus:ring-1 focus:ring-accent/40";
