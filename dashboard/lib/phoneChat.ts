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
 */

/** The id a private Telegram chat carries, which is the sender's user id; null
 *  for a group (negative), a blank or anything that is not a whole number. */
export function privateChatId(chatId: string | undefined): string | null {
  const id = (chatId ?? "").trim();
  return /^[1-9]\d{0,19}$/.test(id) ? id : null;
}

/** The form values with the phone-chat defaults applied. Values are the wire's
 *  strings ("true"/"false" for the switches). Unchanged when chat is off. */
export function withPhoneChat(values: Record<string, string>): Record<string, string> {
  if ((values.chat_enabled ?? "").trim().toLowerCase() !== "true") return values;
  const next: Record<string, string> = { ...values, inbound_enabled: "true" };
  if (!(values.allowed_senders ?? "").trim()) {
    const id = privateChatId(values.chat_id);
    if (id) next.allowed_senders = id;
  }
  return next;
}
