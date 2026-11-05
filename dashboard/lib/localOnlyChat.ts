/**
 * v1.435.0 (chat-first plan, Wave 3 group I, decision D3): Local only
 * changed FROM CHAT, and the default for NEW chats.
 *
 * - "Keep this chat on my PC": the daemon's `chat_local_only` tool writes the
 *   chat's saved setup and answers with a "Local only changed" card keyed
 *   {@link LOCAL_ONLY_CARD_KEY}. The page is the chat's other writer — every
 *   save carries its own switch — so it reads that card and sets its switch,
 *   or its next save would put the old value back and the chat would say
 *   Local only is on while it is not. An Undo of the card puts the switch
 *   back to the card's `old`.
 * - "Start new chats local only": a setting row (`chat_new_local_only`,
 *   settings/schema.py) read once from GET /settings; a new chat starts with
 *   the switch on when it is, else on the user's last choice, as before.
 *
 * Plain data and pure functions (the chat page reads them).
 */

import type { ConfigCard } from "./configCards";

/** The card key `chat_local_only` writes
 *  (src/iron_jarvis/settings/records_history.py LOCAL_ONLY_CARD_KEY). */
export const LOCAL_ONLY_CARD_KEY = "chat.local_only";

/** The setting a new chat reads (settings/schema.py). */
export const NEW_CHATS_LOCAL_ONLY_SETTING = "chat_new_local_only";

/** A NEW chat's Local only switch: on when the setting says so, else the
 *  user's last choice (`ij.chat.localOnly`, as before). */
export function newChatLocalOnly(lastChoice: boolean, settingOn: boolean): boolean {
  return settingOn === true || lastChoice === true;
}

/** What a turn's cards switched Local only to — the newest such card that
 *  is not undone — or null when none of them changed it. */
export function localOnlyFromCards(cards: readonly ConfigCard[] | null | undefined): boolean | null {
  let out: boolean | null = null;
  for (const c of cards ?? []) {
    if (c.kind !== "change" || c.key !== LOCAL_ONLY_CARD_KEY || c.undone) continue;
    if (c.new === "on") out = true;
    else if (c.new === "off") out = false;
  }
  return out;
}

/** After the user pressed Undo on that card: the state it put back, or null
 *  when the card is another one (or not undone). */
export function localOnlyAfterUndo(card: ConfigCard | null | undefined): boolean | null {
  if (!card || card.kind !== "change" || card.key !== LOCAL_ONLY_CARD_KEY || card.undone !== true) return null;
  if (card.old === "on") return true;
  if (card.old === "off") return false;
  return null;
}
