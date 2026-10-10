/**
 * Where a Build pane's Terminal ⇄ Chat toggle sits (calm chat wave 5,
 * v1.329.0).
 *
 * The toggle used to float BELOW the header (`top-10`), over the pane's
 * content, where it cut the right-aligned user bubble of the side chat
 * ("…this quarter? Show"). It now sits IN the header row, just left of the
 * close button, over an empty slot both headers keep for it: the terminal
 * header (TerminalPane, `reserveViewToggle`) and the chat header (the Build
 * page). It is still ONE element on the page, outside every drag handle, so a
 * press keeps its focus when the view flips and can never start a drag.
 *
 * The numbers follow the two headers' shared metrics: a 1px pane border, the
 * header's `px-3` (12px), a 20px close button and the header's `gap-2` (8px):
 * the slot's right edge is 1 + 12 + 20 + 8 = 41px from the pane's edge. The
 * group is two 20px buttons, a 2px gap and 2px of padding each side = 46px
 * wide and 24px tall, centred in the header's content row (1px border + 8px
 * padding + (20 - 24) / 2 = 7px down).
 */

/** The group's own size: two `h-5 w-5` buttons, `gap-0.5`, `p-0.5`. */
export const PANE_TOGGLE_WIDTH_PX = 46;

/** The empty slot a header keeps for the toggle (right before its close
 *  button). Never clickable: the toggle drawn over it is. */
export const PANE_TOGGLE_SLOT = "pointer-events-none w-[46px] shrink-0";

/** The toggle's place over that slot, on the pane's own box. */
export const PANE_TOGGLE_PLACE = "absolute right-[41px] top-[7px]";
