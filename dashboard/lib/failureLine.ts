/**
 * ONE SENTENCE FOR A FAILED TURN (v1.354.0 integrate, G28 joined to the chat).
 *
 * Since v1.354.0 every chat error the daemon sends carries, beside its
 * unchanged `detail`, three plain fields (providers/failure_words.
 * friendly_error, one `chat_turn.error_payload` for both lanes):
 *
 *   title      "OpenRouter refused your key"
 *   fix        "Update it in Settings › Connections."
 *   technical  the server's own text, secret-shaped values masked
 *
 * The chat's failure line says `title. fix` — the SAME sentence a failed
 * agent run's summary ends with ("The run failed: <title>. <fix>",
 * failure_words.plain_reason), which the run page and the bell row print —
 * so one cause reads as one sentence wherever the user meets it. The
 * technical text rides the line's hover title only. A failure that carries
 * no title (an older daemon, the page's own offline / stall words, a
 * "local only" refusal whose daemon sentence IS the words) keeps its
 * message exactly as before.
 *
 * Dependency-free on purpose: `lib/api.ts` is mocked wholesale by dozens of
 * test files and `@/lib/useChatStream` by many more, so this reads the error
 * by shape, never by class.
 */

/** The fields a failed turn may carry (`StreamError` has them all). */
export interface FailureFields {
  title?: unknown;
  fix?: unknown;
  technical?: unknown;
  message?: unknown;
}

function text(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

/** `title. fix` — the join `failure_words.plain_reason` makes (a title that
 *  already ends a sentence keeps its own stop). "" without a title. */
export function failureSentence(title: unknown, fix: unknown): string {
  const t = text(title);
  if (!t) return "";
  const f = text(fix);
  const head = /[.!?]$/.test(t) ? t : `${t}.`;
  return f ? `${head} ${f}` : head;
}

/** The words the chat's failure line shows for *e*: the plain sentence when
 *  the daemon sent one, else the error's own message (unchanged). */
export function turnErrorText(e: unknown): string {
  if (e && typeof e === "object") {
    const f = e as FailureFields;
    const plain = failureSentence(f.title, f.fix);
    if (plain) return plain;
    const msg = text(f.message);
    if (msg) return msg;
  }
  return String(e);
}

/** The technical detail behind that line, for a hover title only ("" when
 *  none, or when the line already IS the raw message). */
export function turnErrorTechnical(e: unknown): string {
  if (!e || typeof e !== "object") return "";
  const f = e as FailureFields;
  if (!failureSentence(f.title, f.fix)) return "";
  return text(f.technical);
}
