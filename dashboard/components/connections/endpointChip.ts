/**
 * v1.329.0 (calm J3, shared in K2): the ONE quiet chip a saved-endpoint row
 * draws its tags with. Settings > Connections uses it for the Anthropic tag,
 * the model and tools / vision; EnvelopeCard uses it for the measurement chip
 * ("measured", "Measuring failed").
 *
 * The rows used to mix two looks: a hairline tag beside filled emerald and
 * amber chips that the light theme did not re-ink, and an amber bordered
 * "floor defaults" chip on every row. Now the shell is the same for all, and
 * only a small mark inside carries a tone token (a check or a dot).
 *
 * v1.330.0 (calm M3): the tags are the calm Badge's shell exactly (no
 * border, no fill), so a saved-endpoint row and the card's status chip are
 * one look; the hairline pill is gone. The row's buttons (Verify tools,
 * Measure, details) are no longer chip-shaped: they are the calm action,
 * the same quiet ghost as every other secondary action on Connections and
 * the same size as the calm Delete beside them.
 *
 * It lives in its own module because tests mock EnvelopeCard with a fixed
 * export list (EnvelopeRowControls, MeasuredEndpoints).
 */

/** The calm secondary action of Connections and Fleet: no border, no fill
 *  until the pointer is on it, the theme's hairline fill on hover, a focus
 *  ring for the keyboard. Same geometry as the calm ConfirmButton (12px,
 *  medium, at least 28px tall), so Sign in, Test or Measure and the calm
 *  Delete / Remove beside them read as ONE row of the same buttons. */
export const CALM_ACTION =
  "inline-flex min-h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-1 text-xs font-medium text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-zinc-300";

/** The same calm action for an icon alone (the up / down arrows): a 28px
 *  square, so a finger can still hit it. */
export const CALM_ICON_ACTION =
  "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-zinc-400";

/** A row tag: the calm Badge's shell (`CALM_BADGE` in components/ui.tsx),
 *  spelled out here so this module imports nothing; a test holds the two
 *  strings equal. */
export const ENDPOINT_CHIP =
  "inline-flex shrink-0 items-center gap-1.5 rounded-full px-1.5 py-0.5 text-[11px] font-medium leading-none text-zinc-400";

/** A saved-endpoint row's button (Verify tools, Measure, details): the calm
 *  action. Kept under this name so both row files keep one import. */
export const ENDPOINT_CHIP_BUTTON = CALM_ACTION;
