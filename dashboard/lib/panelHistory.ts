/**
 * The chat so far as the @-agent panel route reads it (v1.284.0): who said
 * each line (the user, Iron Jarvis, or a panel agent by its key) and the
 * words. The page's third way of sending the chat back to a model, beside the
 * two Iron Jarvis lanes (`toRequestMessages` in app/chat/page.tsx).
 *
 * Idea 39 (39-a, third review): a REPORT (a schedule's run summary, a
 * watcher's file names) is outside words, never Iron Jarvis's own: it wears
 * its label first (`reportRequestLabel`, opening with the prefix the daemon
 * fences, `chat_refs.REPORT_LINE_PREFIX`), so the agent's transcript says it
 * as a report and fences what follows (`agents/threads.chat_transcript`).
 *
 * Pure, React-free.
 */

import { type ReportFrom, reportRequestLabel } from "@/lib/reports";

/** The slice of a chat message this reads. */
export interface PanelHistoryLine {
  role: string;
  content?: string;
  panelWho?: string;
  reportFrom?: ReportFrom;
}

/** One row of the panel route's `history`. */
export interface PanelHistoryRow {
  who: string;
  content: string;
}

/** Empty bubbles (hand-off markers, a stopped turn) carry nothing and are
 *  skipped; the daemon budgets the rest to the speaker's model. */
export function panelHistoryOf(msgs: readonly PanelHistoryLine[]): PanelHistoryRow[] {
  const out: PanelHistoryRow[] = [];
  for (const m of msgs) {
    const words = (m.content || "").trim();
    if (!words) continue;
    const isReport = m.role === "assistant" && !!m.reportFrom;
    const content = isReport && m.reportFrom ? `${reportRequestLabel(m.reportFrom)}\n${words}` : words;
    out.push({ who: m.role === "user" ? "user" : m.panelWho || "jarvis", content });
  }
  return out;
}
