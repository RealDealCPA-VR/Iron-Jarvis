/**
 * A model id in words, for the composer's model chip (calm chat wave 4,
 * v1.329.0).
 *
 * The chip shows the catalog's own label when the row has one ("Opus 5.5").
 * When it has none, the chip used to print the raw id ("claude-opus-4-8"),
 * which reads louder than the quiet chips beside it. This turns the Claude
 * ids into the names people say ("Opus 4.8", "Sonnet 3.5"): only the family
 * and the version numbers the id already spells, never anything else.
 * Every other id comes back unchanged, so nothing is invented, and the raw
 * id stays on the chip's title as the record of what runs.
 */

const FAMILY = "(opus|sonnet|haiku)";
const VER = "(\\d{1,2})(?:[-.](\\d{1,2}))?";
/** A dated snapshot ("-20250514") or a "-latest" tag; neither is said. */
const TAIL = "(?:-\\d{8}|-latest)?";

/** claude-opus-4-8, claude-sonnet-5, claude-haiku-4-5-20251001 */
const NEW_SHAPE = new RegExp(`^claude-${FAMILY}-${VER}${TAIL}$`, "i");
/** claude-3-5-sonnet-20241022, claude-3-opus */
const OLD_SHAPE = new RegExp(`^claude-${VER}-${FAMILY}${TAIL}$`, "i");
/** The million-token form the Claude picker uses ("claude-opus-4-8[1m]"). */
const ONE_M = /\[1m\]$/i;

function cap(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
}

export function friendlyModelName(id: string): string {
  const raw = (id || "").trim();
  if (!raw) return raw;
  const big = ONE_M.test(raw);
  const bare = raw.replace(ONE_M, "");
  let family = "";
  let major = "";
  let minor: string | undefined;
  const a = NEW_SHAPE.exec(bare);
  if (a) {
    [, family, major, minor] = a;
  } else {
    const b = OLD_SHAPE.exec(bare);
    if (!b) return raw;
    [, major, minor, family] = b;
  }
  const name = `${cap(family)} ${major}${minor !== undefined ? `.${minor}` : ""}`;
  return big ? `${name} 1M` : name;
}
