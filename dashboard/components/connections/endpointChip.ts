/**
 * v1.329.0 (calm J3, shared in K2): the ONE quiet chip a saved-endpoint row
 * draws its tags with. Settings > Connections uses it for the Anthropic tag,
 * the model, tools / vision and Verify tools; EnvelopeCard uses it for the
 * measurement chip ("measured", "Measuring failed") and the Measure button.
 *
 * The rows used to mix two looks: a hairline tag beside filled emerald and
 * amber chips that the light theme did not re-ink, and an amber bordered
 * "floor defaults" chip on every row. Now the shell is the same for all, and
 * only a small mark inside carries a tone token (a check or a dot).
 *
 * It lives in its own module because tests mock EnvelopeCard with a fixed
 * export list (EnvelopeRowControls, MeasuredEndpoints).
 */
export const ENDPOINT_CHIP =
  "inline-flex shrink-0 items-center gap-1 rounded-full border border-white/10 px-1.5 py-0.5 text-[11px] leading-none text-zinc-400";

/** The same shell as a ghost button: it fills on hover. */
export const ENDPOINT_CHIP_BUTTON = `${ENDPOINT_CHIP} transition-colors hover:bg-white/[0.06] hover:text-zinc-200 disabled:opacity-50`;
