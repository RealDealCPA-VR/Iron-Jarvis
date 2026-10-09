/**
 * QUOTE a part of a reply into the composer (v1.325.0; the idea is
 * assistant-ui's quote button, MIT — no code taken).
 *
 * Pure text helpers; the floating button is components/chat/QuoteSelection.
 */

/** The longest quote kept, in characters, before "…". */
export const QUOTE_MAX_CHARS = 1500;

/**
 * The selected text as a markdown blockquote: trimmed, line ends unified,
 * runs of blank lines folded to one, every line prefixed with "> " (a blank
 * line becomes a bare ">", so the quote stays ONE block). Capped at
 * QUOTE_MAX_CHARS with an honest "…". Empty in → "".
 */
export function quoteBlock(text: string): string {
  let t = (text ?? "").replace(/\r\n?/g, "\n").trim();
  if (!t) return "";
  t = t.replace(/\n{3,}/g, "\n\n");
  if (t.length > QUOTE_MAX_CHARS) t = `${t.slice(0, QUOTE_MAX_CHARS).trimEnd()}…`;
  return t
    .split("\n")
    .map((line) => {
      const l = line.trimEnd();
      return l ? `> ${l}` : ">";
    })
    .join("\n");
}

/**
 * Put a quote into what the user has already typed. The quote goes AFTER the
 * existing text, as its own paragraph (a blank line between), and the box
 * ends with a blank line so the user's next words are not part of the quote.
 * An empty box gets just the quote. An empty quote changes nothing.
 */
export function insertQuote(current: string, quote: string): string {
  const q = (quote ?? "").trim();
  if (!q) return current ?? "";
  const before = (current ?? "").replace(/\s+$/, "");
  return before ? `${before}\n\n${q}\n\n` : `${q}\n\n`;
}
