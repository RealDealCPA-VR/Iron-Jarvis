/**
 * v1.330.0: the words the composer's reasoning chip says.
 *
 * The chip used to read "Reasoning" with nothing picked, so the user could not
 * tell what level the model would run at. The daemon's catalog row now carries
 * `reasoning_default`: the level the vendor documents for that exact model
 * when NOTHING is sent ("" = unknown, never a guess). This module turns it into
 * words, and nothing else: picking a level still sends exactly that level, and
 * "Default" still sends nothing.
 *
 *  - picked "high"            chip "High"
 *  - nothing, default medium  chip "Medium",       menu "Medium (default)"
 *  - nothing, default off     chip "Thinking off", menu "Thinking off (default)"
 *  - nothing, default auto    chip "Auto",         menu "Auto (default)"
 *  - nothing, unknown         chip "Reasoning",    menu "Default"
 */

const LEVELS = ["low", "medium", "high"];
const KNOWN_DEFAULTS = [...LEVELS, "off", "auto"];

/** The first sentence of every tooltip: what the knob measures. */
export const REASONING_TITLE_LEAD =
  "How hard the model thinks before answering. Higher is slower and costs more.";

/** The row's default if it is one of the words this module knows, else "".
 *  An older daemon sends none and a newer one may send a word this build has
 *  no label for; both read as unknown rather than as a made-up label. */
export function reasoningDefaultOf(row: { reasoning_default?: unknown } | null | undefined): string {
  const v = typeof row?.reasoning_default === "string" ? row.reasoning_default.trim().toLowerCase() : "";
  return KNOWN_DEFAULTS.includes(v) ? v : "";
}

function cap(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/** The default in plain words ("Medium", "Thinking off", "Auto"), "" when unknown. */
export function defaultWords(def: string): string {
  if (LEVELS.includes(def)) return cap(def);
  if (def === "off") return "Thinking off";
  if (def === "auto") return "Auto";
  return "";
}

/** What the chip shows: the picked level, else the model's default, else "Reasoning". */
export function reasoningChipLabel(picked: string, def: string): string {
  if (LEVELS.includes(picked)) return cap(picked);
  return defaultWords(def) || "Reasoning";
}

/** The menu row for "send nothing": names the default when it is known. */
export function reasoningDefaultOption(def: string): string {
  const words = defaultWords(def);
  return words ? `${words} (default)` : "Default";
}

/** The chip's tooltip: what it measures, then what happens with nothing picked. */
export function reasoningTitle(def: string): string {
  if (LEVELS.includes(def)) {
    return `${REASONING_TITLE_LEAD} With nothing picked this model runs at ${def}, its own default.`;
  }
  if (def === "off") {
    return `${REASONING_TITLE_LEAD} With nothing picked this model does not think first. Pick a level to turn thinking on.`;
  }
  if (def === "auto") {
    return `${REASONING_TITLE_LEAD} With nothing picked this model decides for itself how much to think.`;
  }
  return `${REASONING_TITLE_LEAD} With nothing picked the model decides.`;
}
