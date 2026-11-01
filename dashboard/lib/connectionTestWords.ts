/**
 * What a connection Test said, in plain words (v1.420.0, C6.6).
 *
 * `POST /connections/{provider}/test` used to answer only the probe's raw
 * text ("could not reach anthropic: ConnectError: [Errno 11001] …",
 * "openai probe returned HTTP 500: {json}"), which the key door and the
 * Connections page printed as it was. The daemon now adds, beside the
 * unchanged `detail`, a `title` and a `fix` (worded from the probe's typed
 * facts — a refused key and an error status through the same
 * `failure_words.friendly_error` every chat failure uses) and the masked
 * `technical` text. The page shows `title. fix` (lib/failureLine's join) and
 * folds the technical text under Details. An older daemon (no title) keeps
 * its `detail` exactly as before.
 *
 * Dependency-free beyond lib/failureLine (read by shape, never by class).
 */
import { failureSentence } from "./failureLine";

export interface ConnectionTestWords {
  ok: boolean;
  detail: string;
  title?: string;
  fix?: string;
  technical?: string;
  kind?: string;
}

/** The line to show and the technical text to fold ("" for none). `worded`
 *  is false for an older daemon's raw `detail`, so a caller can keep its
 *  old "Connected — " prefix there only. */
export function testWords(r: ConnectionTestWords | null | undefined): {
  text: string;
  technical: string;
  worded: boolean;
} {
  if (!r) return { text: "", technical: "", worded: false };
  const plain = failureSentence(r.title, r.fix);
  if (plain) {
    const tech = typeof r.technical === "string" ? r.technical.trim() : "";
    return { text: plain, technical: tech, worded: true };
  }
  return { text: String(r.detail ?? ""), technical: "", worded: false };
}
