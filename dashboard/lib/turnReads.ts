/**
 * What a chat turn READ besides the conversation (calm chat W2-3, v1.327.0):
 * the project folder's instruction files (`folder_rules`, e.g. ["AGENTS.md",
 * "CLAUDE.md"]) and the saved chats the message pointed to with "@"
 * (`thread_refs`, [{id, title, chars, ok, note}]).
 *
 * Since v1.326.0 the POST /chat response and the /chat/stream done frame
 * ALWAYS carry both keys. The page stores them on the assistant message (so a
 * saved chat keeps them) and the expanded TurnReceipt says them in plain
 * words. Both cross a JSON boundary twice (the wire, then a saved thread read
 * back from disk), so every reader goes through the decoders here: a
 * WHITELIST, like every other receipt field.
 *
 * Pure module, no React: the stream hook, the page and the receipt all import
 * it, and the tests that mock `@/lib/useChatStream` are unaffected.
 */

/** One saved chat this turn was pointed to (daemon/chat_refs.py `_row`). */
export interface ThreadRefReceipt {
  id: string;
  title: string;
  /** Characters of that chat that reached the model (0 when none did). */
  chars: number;
  /** True when the chat was read; false when it was left out (see `note`). */
  ok: boolean;
  /** One plain sentence from the daemon ("" when there is nothing to say). */
  note: string;
}

/** Most rule-file names one receipt keeps (the daemon reads four at most). */
export const FOLDER_RULES_MAX = 8;
/** Most chat rows one receipt keeps (the daemon reads three; it also REPORTS
 *  the ones it left out, so the list can be a little longer). */
export const THREAD_REFS_ROWS_MAX = 12;

function line(raw: unknown, max: number): string {
  if (typeof raw !== "string") return "";
  // One line: a title is user/model-written text.
  return raw.replace(/\s+/g, " ").trim().slice(0, max);
}

/** The `folder_rules` field's normal form: non-blank file names, one line
 *  each, duplicates dropped (first wins), at most FOLDER_RULES_MAX. Anything
 *  that is not a list reads as none. */
export function decodeFolderRules(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const x of raw) {
    const name = line(x, 120);
    if (!name || out.includes(name)) continue;
    out.push(name);
    if (out.length >= FOLDER_RULES_MAX) break;
  }
  return out;
}

/** The `thread_refs` field's normal form: rows with an id, `ok` only when it
 *  is a real `true`, `chars` a finite non-negative whole number. */
export function decodeThreadRefs(raw: unknown): ThreadRefReceipt[] {
  if (!Array.isArray(raw)) return [];
  const out: ThreadRefReceipt[] = [];
  for (const r of raw) {
    if (!r || typeof r !== "object" || Array.isArray(r)) continue;
    const x = r as Record<string, unknown>;
    const id = line(x.id, 80);
    if (!id) continue;
    const chars =
      typeof x.chars === "number" && Number.isFinite(x.chars) && x.chars > 0
        ? Math.floor(x.chars)
        : 0;
    out.push({
      id,
      title: line(x.title, 200),
      chars,
      ok: x.ok === true,
      note: line(x.note, 300),
    });
    if (out.length >= THREAD_REFS_ROWS_MAX) break;
  }
  return out;
}

/** "a", "a and b", "a, b and c". */
function joinWords(xs: string[]): string {
  if (xs.length <= 1) return xs[0] ?? "";
  return `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}

/** The receipt's line for the folder rules, or null when none were read:
 *  "Followed the project's AGENTS.md and CLAUDE.md". */
export function folderRulesLine(raw: unknown): string | null {
  const names = decodeFolderRules(raw);
  return names.length ? `Followed the project's ${joinWords(names)}` : null;
}

/** Notes the daemon gives a reference it left out ON PURPOSE, where nothing
 *  the user wanted was lost (daemon/chat_refs.py NOTE_CURRENT, NOTE_EMPTY).
 *  Every other left-out reference is a WARNING: the answer was written without
 *  a chat the user asked it to read (not found, too many, unreadable, or a
 *  reason this client has not heard of yet). Matched by the words the daemon
 *  sends; a pin keeps the two in step. */
export const QUIET_REF_NOTES: readonly string[] = [
  "This is the chat you are in, so it was not added again.",
  "That chat has no messages yet.",
];

/** True when a reference was left out for a reason the user should SEE. */
export function refWarns(ref: ThreadRefReceipt): boolean {
  if (ref.ok) return false;
  return !QUIET_REF_NOTES.includes(ref.note);
}

/** How many of the turn's references were left out with a warning. */
export function refWarningCount(raw: unknown): number {
  return decodeThreadRefs(raw).filter(refWarns).length;
}

/** The receipt's words for the referenced chats: one "Read N earlier chats:
 *  <titles>" line for the ones read (null when none were), and one line per
 *  reference that carries a note — `warn` only for a left-out one that
 *  warns. */
export function threadRefLines(raw: unknown): {
  read: string | null;
  notes: { key: string; text: string; warn: boolean }[];
} {
  const refs = decodeThreadRefs(raw);
  const read = refs.filter((r) => r.ok);
  const titles = read.map((r) => r.title || "an untitled chat");
  const readLine = read.length
    ? `Read ${read.length} earlier chat${read.length === 1 ? "" : "s"}: ${titles.join(", ")}`
    : null;
  const notes: { key: string; text: string; warn: boolean }[] = [];
  refs.forEach((r, i) => {
    const note = r.note || (r.ok ? "" : "That chat could not be read.");
    if (!note) return;
    notes.push({
      key: `${r.id}-${i}`,
      text: r.title ? `${r.title}: ${note}` : note,
      warn: refWarns(r),
    });
  });
  return { read: readLine, notes };
}
