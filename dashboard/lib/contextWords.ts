/**
 * How full a conversation is, in words — ONE place for the IJ harness's meter
 * (lib/buildContext `meterTitle`) and the chat page's gauge and compaction
 * offer (app/chat/page.tsx), v1.377.0 (IJ harness part 2 Phase 6).
 *
 * The v1.374.0 review: when no pin, measurement, catalog or probe knows a
 * model's window, the number is the app's own SAFE SIZE — the room Iron Jarvis
 * gives the conversation — never "what this model can read at once". The
 * daemon says which (`window_known`); the harness meter said it since
 * v1.374.0, and the chat page's gauge now says the same words.
 *
 * Plain language: no "tokens", no "context window".
 */

/** "fills about N% of …" for a conversation (`noun`), by whether the
 *  model's own limit is known. `windowKnown` false = the safe size. */
export function fillWords(noun: string, percent: number, windowKnown: boolean): string {
  return windowKnown
    ? `This ${noun} fills about ${percent}% of what this model can read at once.`
    : `This ${noun} fills about ${percent}% of what Iron Jarvis sends this model at once. This model's own limit is not known, so a safe size is used.`;
}

/** The chat gauge's words once earlier messages had to be summarized. */
export function trimmedWords(dropped: number, windowKnown: boolean): string {
  const lead = `${dropped} earlier message${dropped === 1 ? " was" : "s were"} summarized so this chat fits`;
  return windowKnown
    ? `${lead} what this model can read at once. A model that reads longer chats would keep them.`
    : `${lead} what Iron Jarvis sends this model at once. This model's own limit is not known, so a safe size is used.`;
}

/** The daemon's `window_known` (absent — an older Iron Jarvis — reads as
 *  known, the words it always had). */
export function windowKnownOf(usage: { window_known?: unknown } | null | undefined): boolean {
  return !(usage && usage.window_known === false);
}
