/**
 * Phone chat on ONE screen (v1.353.0, G56).
 *
 * Turning on two-way Telegram chat used to need the Advanced fold and typing
 * your own Telegram user id into "Allowed senders" — while the page itself
 * said that id is usually the chat ID it had just detected. The Telegram form
 * now shows the chat switch and the allowed senders beside the token and chat
 * ID, and this rule fills the list for the person who ticks chat:
 *
 *   - chat on  → listening on too (the daemon's own rule: chat implies
 *     listening, `routes/comm.py` normalises it at save; the form says the
 *     same thing before the save so the two never disagree);
 *   - chat on AND the allowed senders list is EMPTY AND the chat ID is a
 *     private chat's id → the list becomes that id.
 *
 * Only a private chat counts: in Telegram a private chat's id IS the user's
 * id (a positive number), while a group's id is negative and names nobody, so
 * a group id is never written into the allowlist. A list the person typed is
 * never replaced. The list still fails closed, and it is VISIBLE: the person
 * sees which id may talk to Iron Jarvis before they save.
 *
 * A list the FORM filled follows a corrected chat ID (review, v1.353.0):
 * picking the wrong private chat and then detecting again used to correct
 * the chat ID but leave the other person's id as the only allowed sender.
 * The form notes what it filled under `AUTO_SENDERS_KEY`; while the list
 * still holds exactly that, it is the form's to move. Moving to a group keeps
 * the filled id (in a group the sender is still that person). Once the person
 * edits the list it is theirs. The note is not a field, so the save (which
 * posts only the destination type's own fields) never sends it.
 */

/** Where the form notes the allowed-senders value it filled in itself. */
export const AUTO_SENDERS_KEY = "__phone_chat_auto_senders";

/** v1.353.0 integrate: where the form notes the listening value it had
 *  BEFORE ticking chat switched listening on. Unticking chat puts it back, so
 *  tick-then-untick saves a destination that does not listen (the listening
 *  switch sits under Advanced, where nobody would look). A listening switch
 *  the person turned on themselves is never noted, and stays on. Like the
 *  senders note, it is not a field, so the save never sends it. */
export const AUTO_INBOUND_KEY = "__phone_chat_auto_inbound";

/** The id a private Telegram chat carries, which is the sender's user id; null
 *  for a group (negative), a blank or anything that is not a whole number. */
export function privateChatId(chatId: string | undefined): string | null {
  const id = (chatId ?? "").trim();
  return /^[1-9]\d{0,19}$/.test(id) ? id : null;
}

/** The form values with the phone-chat defaults applied. Values are the wire's
 *  strings ("true"/"false" for the switches). Unchanged when chat is off. */
export function withPhoneChat(values: Record<string, string>): Record<string, string> {
  const listening = (values.inbound_enabled ?? "").trim().toLowerCase() === "true";
  if ((values.chat_enabled ?? "").trim().toLowerCase() !== "true") {
    // Chat off: listening the FORM switched on for chat goes back to what it
    // was (only while it is still on — a later change is the person's).
    const prior = values[AUTO_INBOUND_KEY];
    if (prior === undefined) return values;
    const next: Record<string, string> = { ...values };
    delete next[AUTO_INBOUND_KEY];
    if (listening) next.inbound_enabled = prior;
    return next;
  }
  const next: Record<string, string> = { ...values, inbound_enabled: "true" };
  if (!listening && values[AUTO_INBOUND_KEY] === undefined) {
    next[AUTO_INBOUND_KEY] = values.inbound_enabled ?? "false";
  }
  const list = (values.allowed_senders ?? "").trim();
  const filled = values[AUTO_SENDERS_KEY];
  if (!list || (filled !== undefined && list === filled)) {
    const id = privateChatId(values.chat_id);
    if (id) {
      next.allowed_senders = id;
      next[AUTO_SENDERS_KEY] = id;
    }
  }
  return next;
}
