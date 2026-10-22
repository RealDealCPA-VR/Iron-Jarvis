/**
 * The Build pane's frame (v1.329.0, calm chat wave 5).
 *
 * The chat page has no boxes with glowing accent edges; the Build page around
 * its terminals had one on every pane (border-accent + shadow-glow + ring) and
 * read as a different app. Now a pane is a hairline frame on the theme's ink:
 *
 *  - a file dragged over it: an accent hairline, because a drop target must
 *    say "here" while something is held over it;
 *  - the focused pane: a slightly brighter hairline (it is the only pane on
 *    screen, so an accent edge would only be decoration — v1.369.0 retired
 *    the free-form canvas, the one place several overlapped);
 *  - every other pane: the quiet hairline, a touch brighter on hover.
 *
 * No glow, no ring, no shadow card. Theme tokens only (`white` is remapped on
 * the light Marks), so Daylight draws a dark hairline on light ink. The
 * terminal canvas inside keeps its own dark ground; this is only the edge.
 * Used by BOTH layers of a pane (TerminalPane and the page's chat layer) so
 * the frame never changes when the view flips.
 */
export function paneFrameClass(focused: boolean, dragOver = false): string {
  if (dragOver) return "border-accent/60";
  if (focused) return "border-white/[0.1]";
  return "border-white/[0.06] hover:border-white/[0.12]";
}
