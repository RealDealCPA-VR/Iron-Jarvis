/**
 * Markdown -> one line of plain words (v1.358.0, lane 8A), DEPENDENCY-FREE.
 *
 * THE FAILURE THIS ANSWERS. A run's summary is model-written markdown. The
 * Schedules row flattened it (lib/scheduleOutcome.scheduleDetail), but the
 * bell printed the same text raw, so a weekly review's row read
 * "# Final Report ## What happened ..." in the notifications. The bell is on
 * every page and must not pull the chat's Markdown renderer (react-markdown,
 * the draft and chart cards) into every page's bundle, which is why it could
 * not simply call `components/Markdown.plainText`.
 *
 * So the flattening lives HERE, with no imports at all: `plainText` is the
 * exact rule `components/Markdown.plainText` applies (that file can re-export
 * this one), and `summaryLine` is the row rule the Schedules page uses — a
 * report flattened onto one line keeps its heading marks mid-line, where the
 * line-start rule cannot see them, so they are dropped first. The Schedules
 * row (`scheduleDetail`) and the bell's rows read `summaryLine`: one helper.
 */

/**
 * Markdown -> one line of plain text, for a row that truncates. Strips the
 * markers a model writes (emphasis, headings, list bullets, links, inline
 * code, fences) and collapses whitespace; the words stay. Snake_case
 * identifiers keep their underscores (only word-bounded `_x_` is emphasis).
 */
export function plainText(md: string): string {
  return (md || "")
    .replace(/```[^\n]*\n?/g, "") // fence markers (the code inside stays)
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1") // images -> alt
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") // links -> text
    .replace(/^\s{0,3}#{1,6}\s+/gm, "") // headings
    .replace(/^\s{0,3}>\s?/gm, "") // blockquotes
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, "") // list bullets
    .replace(/(\*\*|__)(?=\S)([\s\S]+?)(?<=\S)\1/g, "$2") // bold
    .replace(/\*(?=\S)([^*]+?)(?<=\S)\*/g, "$1") // *italic*
    .replace(/(?<![\w])_(?=\S)([^_]+?)(?<=\S)_(?![\w])/g, "$1") // _italic_
    .replace(/~~(.+?)~~/g, "$1") // strikethrough
    .replace(/`([^`]+)`/g, "$1") // inline code
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * A run's summary (or a row's detail) as the plain words a one-line row
 * shows. New rows are already a plain sentence; an older one, or a summary
 * the model wrote, may hold a report's markdown flattened onto one line
 * ("# Final Report ## What happened ..."), whose heading marks sit mid-line
 * where `plainText`'s line-start rule cannot see them — dropped first.
 * Anything that is not a string reads as "".
 */
export function summaryLine(text: unknown): string {
  const raw = typeof text === "string" ? text : "";
  const flat = raw.replace(/(^|\s)#{1,6}(?=\s)/g, "$1");
  return plainText(flat);
}
