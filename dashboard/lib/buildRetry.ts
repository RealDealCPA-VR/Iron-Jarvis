/**
 * RETRY IN A CODING SESSION (harness gaps, fix 2).
 *
 * A Build coding session's failed reply used to say "Press Retry" (the
 * daemon's generic fix, `failure_words.GENERIC_FIX`, and the restart line
 * `durable_turns.INTERRUPTED_DETAIL`) with no Retry anywhere on the screen.
 * The chat's rule, at a session's scale: the NEWEST reply that failed or was
 * cut off offers one Retry, which sends THAT reply's question again — its
 * words and what rode them (the uploads, the "@" files, the "/" skill; a
 * Claude Code command as it was typed) — as a new turn. Never while a reply
 * runs, never on an archived session, never on a safety refusal (the same
 * conversation is declined again: Rewind is that line's way out) and never
 * beside an account's limit card (Resume / Choose account are its way out).
 *
 * The daemon writes the transcript, so the failed attempt stays above the new
 * one — what happened is kept, never rewritten.
 *
 * Pure; no React, no fetch.
 */

import { pickedSkillOf } from "@/lib/buildRewind";
import { atFileFromPath, fileFromPath } from "@/lib/buildNext";
import { KEEP_GOING_TEXT, type BuildMessage } from "@/lib/buildSessions";
import type { QueuedItem } from "@/lib/buildComposer";

/** The press's words — the ones the daemon's sentences already say. */
export const RETRY_WORDS = "Retry";

/** Does this reply offer Retry? `newest`: it is the conversation's last
 *  message. */
export function retryOffered(
  m: BuildMessage | null | undefined,
  o: { newest: boolean; busy: boolean; archived: boolean },
): boolean {
  if (!m || m.role !== "assistant" || !o.newest || o.busy || o.archived) return false;
  if (m.errorKind === "refusal") return false;
  if (m.accountLimit) return false;
  // v1.397.0 review: a reply that tried the work on several models keeps its
  // card (a finished attempt can still be kept); Retry would start every
  // attempt again — new branches, new spend — and a "Try on…" message's
  // retry was a plain turn on the session's own model.
  if (m.attempts) return false;
  return (typeof m.error === "string" && m.error.trim() !== "") || m.interrupted === true;
}

function strs(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.trim() !== "") : [];
}

/**
 * What Retry sends for the reply at `index` of `shown`: its question, found
 * by the reply's turn in the WHOLE transcript (`all` — a daemon's own "carry
 * on" question is not shown, and its retry is "continue", the same as Keep
 * going), else the nearest question shown above it. Null when there is no
 * question to send (nothing to retry).
 */
export function retryItemFor(
  shown: readonly BuildMessage[],
  index: number,
  all: readonly BuildMessage[],
  folder: string,
): QueuedItem | null {
  const reply = shown[index];
  if (!reply || reply.role !== "assistant") return null;
  let q: BuildMessage | undefined;
  if (reply.turnId) {
    q = all.find((m) => m.role === "user" && !m.steer && m.turnId === reply.turnId);
  }
  if (!q) {
    for (let j = index - 1; j >= 0; j -= 1) {
      const m = shown[j];
      if (m.role === "user" && !m.steer) {
        q = m;
        break;
      }
    }
  }
  if (!q) return null;
  if (q.continuation === true) return { text: KEEP_GOING_TEXT };
  const skillSource = (q as { skillSource?: unknown }).skillSource;
  const skill = pickedSkillOf(q.skill, skillSource);
  const files = strs(q.filePaths).map((p) => atFileFromPath(p, folder));
  const attachments = strs(q.attachmentPaths).map(fileFromPath);
  const content = typeof q.content === "string" ? q.content : "";
  // Harness gaps review: what the question CARRIED, never a new reading of
  // its words. A Claude Code command question is saved as typed ("/review
  // app.py") with its `command`: the command goes by name and its words are
  // what followed the name — re-reading the line through the composer lost
  // the command whenever the session's command list could not be read. Any
  // other question's words go as they are (`resend`): a skill's words that
  // start with "/model" ran the session's own /model command.
  const command = typeof q.command === "string" ? q.command.trim() : "";
  const text = command ? commandWords(content, command) : content;
  if (!command && !text.trim() && !skill && files.length === 0 && attachments.length === 0) return null;
  return {
    text,
    ...(skill ? { skill } : {}),
    ...(files.length ? { files } : {}),
    ...(attachments.length ? { attachments } : {}),
    resend: { command },
  };
}

/** The words a command question carried: what followed "/<command>" in the
 *  line as it was typed (the daemon writes it so); the whole line when it
 *  does not start with the command's name. */
function commandWords(content: string, command: string): string {
  const line = content.trim();
  const head = `/${command}`;
  if (line.toLowerCase().startsWith(head.toLowerCase()) && (line.length === head.length || /\s/.test(line[head.length]))) {
    return line.slice(head.length).trim();
  }
  return line;
}
