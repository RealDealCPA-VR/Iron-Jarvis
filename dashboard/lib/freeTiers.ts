/**
 * Which key providers have a FREE TIER (v1.420.0, C6.12).
 *
 * The first-run key door said "free" only for Ollama, so a new user with no
 * plan and no GPU never learned that a Gemini key costs nothing to start
 * with. Nothing in the daemon's catalog records a free tier (providers/* has
 * no such fact), so this is a documented constant, never a guess: each entry
 * names the vendor page that says so and the day it was read. A provider is
 * listed only when its OWN pricing page offers a free tier for API keys.
 *
 * Re-read the page before trusting an entry older than a few months; remove
 * the entry (the door then says nothing) the day the vendor drops the tier.
 */

export interface FreeTier {
  /** The words the door shows beside the provider. */
  words: string;
  /** The vendor page that says the tier exists. */
  source: string;
  /** When that page was read (ISO date). */
  checked: string;
  /** What the tier costs in privacy, said beside it ("" for nothing). Only
   *  what the vendor's own pages say (`noteSources`), never from memory. */
  note: string;
  /** The vendor pages the note is read from, on `checked`. */
  noteSources: readonly string[];
}

export const FREE_TIERS: Record<string, FreeTier> = {
  // Read 2026-10-31. The pricing page has a "Free Tier" column ("Free of
  // charge" for the Flash, Flash-Lite and 2.5 models; "Not available" for the
  // Pro previews and the image models) and a row "Used to improve our
  // products" reading "Yes" on the Free Tier and "No" on the Paid Tier. The
  // Gemini API Additional Terms, for Unpaid Services: Google "uses the content
  // you submit to the Services and any generated responses to provide,
  // improve, and develop" its products, and "human reviewers may read,
  // annotate, and process your API input and output".
  google: {
    words: "free tier",
    source: "https://ai.google.dev/gemini-api/docs/pricing",
    checked: "2026-10-31",
    note:
      "On the free tier Google uses what you send to improve its products, " +
      "and people at Google may read it.",
    noteSources: [
      "https://ai.google.dev/gemini-api/docs/pricing",
      "https://ai.google.dev/gemini-api/terms",
    ],
  },
};

/** The free-tier words for *provider*, or "" when it has none on record. */
export function freeTierWords(provider: string): string {
  return FREE_TIERS[provider]?.words ?? "";
}

/** The tier's privacy note for *provider*, or "". */
export function freeTierNote(provider: string): string {
  return FREE_TIERS[provider]?.note ?? "";
}
