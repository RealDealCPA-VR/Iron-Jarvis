/**
 * CONTINUE a cut-off reply (v1.323.0; the idea is assistant-ui's, MIT — no
 * code taken).
 *
 * A reply that ran out of output room (or was stopped) offers Continue. The
 * press sends a hidden user turn (`continuation: true`, CONTINUE_PROMPT)
 * asking the model to carry on; when its answer lands, `mergeContinuation`
 * makes the cut reply, the hidden turn and the new answer ONE reply — so the
 * conversation reads, saves and is sent next time as if the answer had never
 * stopped.
 *
 * Lives in lib/ because a Next page module may only export its route's own
 * symbols.
 */

/** The hidden turn a Continue press sends. */
export const CONTINUE_PROMPT =
  "Your last reply was cut off. Continue it from exactly where it stopped — do not repeat anything already written and do not add a preamble.";

/** The fields of a chat message the merge reads and writes. */
export interface ContinuableMessage {
  role: "user" | "assistant";
  content: string;
  steer?: boolean;
  continuation?: boolean;
  toolsUsed?: string[];
  documents?: string[];
  thinking?: string;
  interrupted?: boolean;
}

/**
 * Join a cut reply and its continuation. Both ends arrive TRIMMED (the page
 * trims every reply), so the space between two words is gone: one comes back
 * unless the cut already ends on whitespace or an opening bracket/dash, or
 * the continuation starts with closing punctuation. A word cut in half gains
 * a space — rarer, and readable, where gluing two words is not.
 */
export function joinContinuation(cut: string, more: string): string {
  if (!cut) return more;
  if (!more) return cut;
  if (/[\s([{"'“‘\-/]$/.test(cut) || /^[\s.,;:!?)\]}"'”’…%]/.test(more)) return cut + more;
  return `${cut} ${more}`;
}

/**
 * A no-op unless the newest message is an assistant reply answering a hidden
 * continuation turn that directly follows an assistant reply (steer notes
 * read during the continuation may sit between; they stay, in order, before
 * the merged reply).
 */
export function mergeContinuation<T extends ContinuableMessage>(list: T[]): T[] {
  const n = list.length;
  const reply = list[n - 1];
  if (!reply || reply.role !== "assistant") return list;
  let ci = n - 2;
  while (ci >= 0 && list[ci].steer) ci -= 1;
  if (ci < 1 || !list[ci].continuation) return list;
  const cut = list[ci - 1];
  if (cut.role !== "assistant") return list;
  const union = (a?: string[], b?: string[]) => {
    const all = [...(a ?? []), ...(b ?? [])];
    return all.length ? Array.from(new Set(all)) : undefined;
  };
  const merged: T = {
    ...reply,
    content: joinContinuation(cut.content, reply.content),
    toolsUsed: union(cut.toolsUsed, reply.toolsUsed),
    documents: union(cut.documents, reply.documents),
    thinking: [cut.thinking, reply.thinking].filter(Boolean).join("\n\n") || undefined,
    interrupted: undefined,
  };
  return [...list.slice(0, ci - 1), ...list.slice(ci + 1, n - 1), merged];
}
