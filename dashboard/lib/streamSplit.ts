/**
 * Where the FINISHED markdown of a streaming reply ends (v1.250.0, S-03).
 *
 * A streamed reply used to be re-parsed in full on every update, so the cost
 * of one more word grew with the length of the answer. Everything before the
 * last blank line that sits OUTSIDE a code fence is settled markdown and can
 * be rendered once (memoized); only the tail after it keeps changing.
 *
 * Returns the index where the tail begins, or 0 when nothing has settled.
 *
 * Two rules keep the rendering identical to a single parse:
 *   - the cut is always a BLANK LINE, so no block (a list, a table, a fenced
 *     code block) is ever split across the two renders;
 *   - the tail must hold real text, because the streaming caret is an
 *     `::after` on the last child and an empty tail would hang it on a blank
 *     line of its own.
 *
 * Lives in lib/ rather than in the chat page because a Next page module may
 * only export its route's own symbols.
 */
export function settledSplit(content: string): number {
  let fence = false;
  let lastBlank = 0;
  let pos = 0;
  const lines = content.split("\n");
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (/^\s*(```|~~~)/.test(line)) fence = !fence;
    if (!fence && line.trim() === "" && i > 0) {
      const rest = lines.slice(i + 1).join("\n");
      if (rest.trim() !== "") lastBlank = pos + line.length + 1;
    }
    pos += line.length + 1;
  }
  return lastBlank;
}
