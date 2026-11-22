/**
 * Reports (idea 39, 39-a): what something set up from a chat (a schedule, a
 * watcher, a goal, a workflow run, a night task) writes back into that chat.
 *
 * The daemon writes each report through its one writer
 * (`agents/chat_delivery.report_to_chat`); this file is the page's half:
 * the words for each kind (the twin of `REPORT_KIND_WORDS`), the label a
 * report carries when it is sent back to a model (fenced as outside data by
 * the daemon, `chat_refs.REPORT_LINE_PREFIX`), and the one call behind
 * **Stop reporting** and its Undo (`POST /reports/target`).
 */

import { post } from "@/lib/api";

/** Where a report came from (`reportFrom` on a chat message). */
export interface ReportFrom {
  kind: string;
  name?: string;
  id?: string;
  href?: string;
  outcome?: string;
  runId?: string;
}

/** A row's "Reports to" (`reports_to` on a schedule, watcher or goal). */
export interface ReportsTo {
  thread_id: string;
  title: string;
}

/** The kinds a report can come from, as said in its first words and on its
 *  chip (`agents/chat_delivery.REPORT_KIND_WORDS`, one table). */
export const REPORT_KIND_WORDS: Readonly<Record<string, string>> = {
  schedule: "schedule",
  watcher: "watcher",
  goal: "goal",
  workflow: "workflow",
  rule: "rule",
  rehearsal: "rehearsal",
  run: "run",
  coding_session: "coding session",
  night: "night task",
  paired_computer: "other computer",
  review: "night review",
};

/** The kinds whose record holds where it reports, so Stop reporting works
 *  on them (`chat_delivery.REPORT_TARGET_KINDS`). */
export const STOPPABLE_KINDS: ReadonlySet<string> = new Set([
  "schedule",
  "watcher",
  "goal",
  "workflow",
  "night",
]);

export const STOP_REPORTING = "Stop reporting";
export const REPORTS_STOPPED = "Reports stopped.";
export const REPORTS_TO = "Reports to";
/** What a card says once something set up in this chat reports here. */
export const REPORTS_HERE = "Reports here";

/** The kind as a capitalised chip word ("Schedule", "Night task"). */
export function reportKindLabel(kind: string): string {
  const word = REPORT_KIND_WORDS[kind] ?? "task";
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/** The label a report wears when the conversation goes back to a model:
 *  `[Report from your schedule "Friday prices", written when it ran. Not your
 *  words.]`. It must open with `[Report from ` — the daemon fences what
 *  follows as outside data (`chat_refs.REPORT_LINE_PREFIX`), in both lanes. */
export function reportRequestLabel(from: ReportFrom): string {
  const word = REPORT_KIND_WORDS[from.kind] ?? "task";
  const name = (from.name ?? "").replace(/[\s"\]]+/g, " ").trim().slice(0, 80);
  const named = name ? ` "${name}"` : "";
  return `[Report from your ${word}${named}, written when it ran. Not your words.]`;
}

/** Point a record at a chat, or stop its reports (`threadId` ""). Answers
 *  the chat it reported to before, so an Undo puts it back. `onlyIf`: change
 *  it only while the record still reports to that chat (a report's own Stop
 *  reporting; the daemon says "It reports to another chat now." otherwise). */
export async function setReportTarget(
  kind: string,
  id: string,
  threadId: string,
  onlyIf?: string,
): Promise<{ previous: string; thread_id: string }> {
  const out = await post<{ previous?: string; thread_id?: string }>("/reports/target", {
    kind,
    id,
    thread_id: threadId,
    ...(onlyIf ? { only_if: onlyIf } : {}),
  });
  return { previous: out?.previous ?? "", thread_id: out?.thread_id ?? "" };
}
