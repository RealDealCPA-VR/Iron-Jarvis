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
 *  - nothing, default medium  chip "Medium",       menu "Default (Medium)"
 *  - nothing, default off     chip "Thinking off", menu "Default (Thinking off)"
 *  - nothing, default auto    chip "Model decides", menu "Default (Model decides)"
 *  - nothing, unknown         chip "Reasoning",    menu "Default"
 *
 * v1.330.0 (W11): the "send nothing" row LEADS with "Default". It used to say
 * "Medium (default)" right above the explicit "Medium" row, which read as the
 * same choice twice. They are not the same: the default row sends nothing, so
 * the model keeps following its vendor's default (and a chat saved with it
 * follows a different model's default after a switch), while "Medium" pins
 * medium. Both rows stay, so a saved explicit pick still matches an option.
 *
 * v1.356.0 (G75): the "auto" default reads "Model decides". "Auto" was the
 * name of three different controls on one toolbar (this chip, Auto tools and
 * the Auto model), and only this one meant "the model picks how hard to
 * think".
 *
 * v1.401.2 (the user: "in effort it shows the options default, low, medium
 * and high. Default should not be there and there should be Extra in models
 * that allow for it"): the list holds the LEVELS only — no "Default" row.
 * With nothing picked the chip still names the model's own level when it is
 * known (and the list marks it), and still sends nothing. "Extra" is Claude
 * Code's own word for its `xhigh` level (its effort picker, read in 2.1.311);
 * the daemon offers it only for a model whose vendor says it takes it.
 */

/** The levels, in order; `xhigh` only where a model's row offers it. */
const LEVELS = ["low", "medium", "high", "xhigh"];
/** The levels a model runs at by default: a vendor's documented default is
 *  never `xhigh` (none documents one), so it is not among them. */
const BASE_LEVELS = ["low", "medium", "high"];
const KNOWN_DEFAULTS = [...BASE_LEVELS, "off", "auto"];

/** What a level is called, in the menu and on the chip. */
export const LEVEL_WORDS: Readonly<Record<string, string>> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra",
};

/** A level's words ("Extra" for xhigh), "" for a word this build does not know. */
export function levelWords(level: string): string {
  return LEVEL_WORDS[level] ?? "";
}

/** v1.356.0 (G75): the words for a model that picks its own level. */
export const MODEL_DECIDES = "Model decides";

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

/** The default in plain words ("Medium", "Thinking off", "Model decides"), "" when unknown. */
export function defaultWords(def: string): string {
  if (BASE_LEVELS.includes(def)) return levelWords(def);
  if (def === "off") return "Thinking off";
  if (def === "auto") return MODEL_DECIDES;
  return "";
}

/**
 * v1.400.0 (review): what the chip says in a composer toolbar out of room
 * (lib/toolbarFit's `short` step). `null` = its words stay: a level ("Low",
 * "Medium", "High") is already short. Otherwise the chip shows a brain icon,
 * with "Off" for a model that does not think first, and nothing more for
 * "Reasoning" or "Model decides" (the tooltip leads with the whole words).
 * The first cut cut the words to about four letters ("Reaso…"), which saved
 * seven pixels for "Reasoning" and read as a broken word.
 */
export function reasoningShortLabel(picked: string, def: string): string | null {
  if (LEVELS.includes(picked) || LEVELS.includes(def)) return null;
  return def === "off" ? "Off" : "";
}

/** What the chip shows: the picked level, else the model's default, else "Reasoning". */
export function reasoningChipLabel(picked: string, def: string): string {
  if (LEVELS.includes(picked)) return levelWords(picked);
  return defaultWords(def) || "Reasoning";
}

/** v1.401.2: the level the list MARKS — the picked one, else the model's own
 *  default when it is one of the offered levels, else none (""). Marking the
 *  default sends nothing: picking it again changes nothing. */
export function reasoningShownLevel(levels: readonly string[], picked: string, def: string): string {
  if (picked && levels.includes(picked)) return picked;
  return BASE_LEVELS.includes(def) && levels.includes(def) ? def : "";
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
