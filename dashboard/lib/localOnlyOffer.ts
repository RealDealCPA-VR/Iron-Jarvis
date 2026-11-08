/**
 * LOCAL ONLY, OFFERED ONCE (v1.455.0, readiness Wave G, item 25).
 *
 * A CPA firm's first question about a client's document is "where does this
 * go?". When a chat that is not Local only gets a file attached, and a model
 * that stays on this PC or the user's own network is connected, the composer
 * says so in ONE quiet line with a "Keep this chat on this PC" press — never
 * automatic: the press is the user's choice, exactly the chip's own toggle.
 *
 * ONCE: the line is shown in the first chat it applies to (remembered in
 * this browser, `OFFER_SEEN_KEY`), stays for as long as that chat's files
 * are on the box, and is gone for good once answered (Keep or Not now) or
 * once a later chat would show it again. The Local only chip, the ⋯ menu and
 * the setting for new chats stay where they were.
 *
 * Import-free apart from lib/localOnly (pure).
 */

import { staysOnThisNetwork, type LocalOnlyRow } from "@/lib/localOnly";

/** localStorage key: the offer was shown once. */
export const OFFER_SEEN_KEY = "ij.chat.localOnlyOffer";

export const LOCAL_ONLY_OFFER_LINE =
  "This file goes to the model that answers. A model on this PC or your own network is connected, so this chat can stay local.";
export const LOCAL_ONLY_OFFER_KEEP = "Keep this chat on this PC";
export const LOCAL_ONLY_OFFER_DISMISS = "Not now";

/** Is there a connected model that stays on this PC or the user's network
 *  (never the demo model, never a row the catalog greys out)? */
export function hasLocalModel(models: readonly (LocalOnlyRow & { provider?: string })[]): boolean {
  return models.some(
    (m) => m && m.provider !== "mock" && m.available !== false && staysOnThisNetwork(m),
  );
}

/** Should the line show? `seen` = it was already shown in another chat;
 *  `shownHere` = it is this chat's (it keeps showing until answered). */
export function shouldOfferLocalOnly(opts: {
  attachedCount: number;
  localOnly: boolean;
  models: readonly (LocalOnlyRow & { provider?: string })[];
  seen: boolean;
  shownHere: boolean;
  answered: boolean;
}): boolean {
  if (opts.answered || opts.localOnly || opts.attachedCount <= 0) return false;
  if (opts.seen && !opts.shownHere) return false;
  return hasLocalModel(opts.models);
}

/** Was the offer shown before (this browser)? Never throws. */
export function readOfferSeen(): boolean {
  try {
    return window.localStorage.getItem(OFFER_SEEN_KEY) === "1";
  } catch {
    return false;
  }
}

/** Remember that the offer was shown. Never throws. */
export function markOfferSeen(): void {
  try {
    window.localStorage.setItem(OFFER_SEEN_KEY, "1");
  } catch {
    /* storage blocked: it may show again */
  }
}
