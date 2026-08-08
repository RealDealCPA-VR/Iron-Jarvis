/** The "/" skill-picker token, extracted so it can be unit-tested.
 *
 * Until v1.105.0 the chat composer opened the skill picker on
 * `input.startsWith("/")` — a skill could only be invoked when "/" was the very
 * first character of an empty composer, so you could not write the prompt you
 * wanted and then reach for a skill part-way through it.
 *
 * The rule here is the one every mention/command picker uses: a "/" opens a
 * token only when it OPENS A WORD, and the token holds no further "/". That is
 * what keeps ordinary typing from flickering a dropdown — `http://x`,
 * `C:/Users`, `and/or` and `24/7` all have a "/" that follows a non-space
 * character, so none of them match.
 */
export interface SlashToken {
  /** Index OF the "/" — splice from here to drop the token. */
  start: number;
  /** Caret offset (clamped): the token is everything up to the cursor. */
  end: number;
  /** Lowercased text after the "/", for filtering. */
  query: string;
}

const TOKEN = /(^|\s)\/([^\s/]*)$/;

/** The same rule for any trigger character (v1.150.0 — "@" for agents).
 *
 * Generalised rather than copied: "/" and "@" want IDENTICAL behaviour (open
 * only at a word boundary, no second trigger inside the token, caret-clamped),
 * and two regexes would be two definitions that drift on exactly the awkward
 * inputs — `and/or`, `C:/Users`, `email@example.com` — each was written to
 * reject. `slashTokenAt` stays as-is so nothing that already calls it changes.
 */
export function tokenAt(
  text: string,
  caret: number,
  trigger: string,
): SlashToken | null {
  const esc = trigger.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(^|\\s)${esc}([^\\s${esc}]*)$`);
  const pos = Math.max(0, Math.min(caret, text.length));
  const m = re.exec(text.slice(0, pos));
  if (!m) return null;
  return { start: m.index + m[1].length, end: pos, query: m[2].toLowerCase() };
}

/**
 * The "/" token *text*'s caret is sitting in, or null.
 *
 * `caret` is clamped because `input` also changes programmatically (sending
 * clears it, voice replaces it, a saved draft restores it) and those paths do
 * not move the caret, so a stale offset can outrun the text it indexes into.
 */
export function slashTokenAt(text: string, caret: number): SlashToken | null {
  const pos = Math.max(0, Math.min(caret, text.length));
  const m = TOKEN.exec(text.slice(0, pos));
  if (!m) return null;
  return {
    start: m.index + m[1].length,
    end: pos,
    query: m[2].toLowerCase(),
  };
}

/**
 * Remove a picked token from the message, keeping everything around it.
 *
 * The composer used to do `setInput("")` on pick. That was invisibly correct
 * while "/" could only lead a message (the token WAS the whole message) and
 * would silently eat an already-written prompt the moment "/" is allowed
 * mid-sentence.
 */
export function spliceToken(text: string, tok: SlashToken | null): string {
  if (!tok) return "";
  let before = text.slice(0, tok.start);
  const after = text.slice(tok.end);
  // A token picked mid-sentence sits between two spaces ("use /fin on this"),
  // so removing it verbatim leaves a double space behind. Collapse the pair.
  // A token at the END keeps its leading space — the caret lands there and the
  // user carries on typing.
  if (before.endsWith(" ") && after.startsWith(" ")) before = before.slice(0, -1);
  return before + after;
}
