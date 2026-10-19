/**
 * THE RUN PAGE READS AS A RECEIPT (v1.354.0, product-readiness G20).
 *
 * The session page was titled "Session / session_7b32…", printed raw phase
 * ids, tool-id chips and `JSON.stringify(payload)` for most live events, and
 * had no duration. These are its words, kept out of the App Router page
 * (a page may not carry extra named exports) so they can be pinned on their
 * own. Every word comes from a table that already exists:
 *
 *   a tool step       lib/stepWords (the chat's own step phrases)
 *   an event          components/chat/stepLabel, then lib/eventWords (the
 *                     Time-travel feed's words)
 *   a finished run    lib/statusWords (the one status table)
 *   a model's place   lib/ranOnWords.endpointLabel (the model menu's names)
 */

import type { IJEvent } from "@/lib/types";
import { plainText } from "@/components/Markdown";
import { stepLabel } from "@/components/chat/stepLabel";
import { humanSummary } from "@/lib/eventWords";
import { stepPhrase, stepPhraseText } from "@/lib/stepWords";
import { statusWords } from "@/lib/statusWords";
import { providerDisplay } from "@/lib/onboarding";
import { endpointLabel, type RanOnRow } from "@/lib/ranOnWords";
import { normalizeIso } from "@/lib/format";
import { CONTINUE_AFTER_RESTART } from "@/components/InterruptedJobs";

/** The longest title the page header shows; the rest rides the title's hint. */
export const RUN_TITLE_MAX = 80;

/** What the page calls a run before it has loaded (and one with no task). */
export const RUN_TITLE_FALLBACK = "Run";

/* v1.354.0 review: the wrapping a run's task carries that the user never
 * typed. The chat opens an agent run with `${recap}\n\n---\n\n${task}` when the
 * conversation already had messages (app/chat/page.tsx conversationRecap:
 * "Conversation so far:\nUser: …\nAssistant: …"), and a "/"-picked skill
 * leads with its directive line; the daemon's continue door appends its own
 * model-facing recap ("\n\n[Continuing an earlier session. Original task: …]",
 * agents/orchestrator.continue_session). Titled on the raw task, a run opened
 * from an ongoing chat read "Conversation so far:". The recap's own messages
 * may hold a "---" rule (models write them), so the ask is what follows the
 * LAST separator. Pinned against the chat page's source in
 * run-page-words-v13540. */
export const CHAT_RECAP_HEAD = "Conversation so far:";
export const CHAT_RECAP_SEP = "\n\n---\n\n";
const SKILL_LINE_RE = /^Use the "[^"\n]+" skill for this — load it with skill_load first\.\s*/;
const CONTINUE_RECAP_MARK = "\n\n[Continuing an earlier session.";

/** What the user asked a run, without the wrapping the chat and the continue
 *  door add around it (above). Unwrapped text is returned as it is. */
export function askedText(task: string | null | undefined): string {
  let t = (task ?? "").replace(/\r\n/g, "\n");
  if (t.startsWith(CHAT_RECAP_HEAD)) {
    const at = t.lastIndexOf(CHAT_RECAP_SEP);
    if (at >= 0) t = t.slice(at + CHAT_RECAP_SEP.length);
  }
  t = t.replace(SKILL_LINE_RE, "");
  const cont = t.indexOf(CONTINUE_RECAP_MARK);
  if (cont > 0) t = t.slice(0, cont);
  return t;
}

/**
 * The run's title: the FIRST non-blank line of what it was asked, as plain
 * text (a model or a person may write markdown), clipped. `full` is the whole
 * task as one plain line when the title had to drop anything (more lines, or
 * a clip) — the header shows it on demand — else null. The chat's recap and
 * the continue door's recap are not part of the ask (askedText).
 */
export function runTitle(task: string | null | undefined): { title: string; full: string | null } {
  const raw = askedText(task);
  const first = raw.split("\n").find((line) => line.trim()) ?? "";
  const line = plainText(first);
  if (!line) return { title: RUN_TITLE_FALLBACK, full: null };
  const clipped = line.length > RUN_TITLE_MAX;
  const title = clipped ? `${line.slice(0, RUN_TITLE_MAX - 1).trimEnd()}…` : line;
  const whole = plainText(raw);
  const more = clipped || whole !== line;
  return { title, full: more ? whole.slice(0, 600) : null };
}

/** The Model field's words: the demo model in words; a model server the user
 *  set up (a fleet node, or the "custom" endpoint) by the name the model
 *  menu shows for it ("DGX Spark · llama-3-70b"); any other provider in its
 *  plain word ("OpenAI · gpt-x"). The raw `provider / model` stays the
 *  field's title — the record of what ran. */
export function runModelWords(
  provider: string | null | undefined,
  model: string | null | undefined,
  catalog: readonly RanOnRow[] | null | undefined,
): string {
  const p = (provider ?? "").trim();
  const m = (model ?? "").trim();
  if (p === "mock") return providerDisplay(p);
  const endpoint = p.startsWith("fleet-") || p === "custom";
  let where = endpoint ? endpointLabel(p, catalog) : providerDisplay(p);
  // A row that names its MODEL in `name` is not a place.
  if (!where || where === m) where = providerDisplay(p);
  return m ? `${where} · ${m}` : where;
}

/** One live event, in words: what happened (`label`) and a quiet detail
 *  (`detail`, "" when the label says it all). Never the payload as JSON —
 *  the raw type stays on the row's hover. */
export function liveEventWords(e: IJEvent): { label: string; detail: string } {
  const p = (e.payload ?? {}) as Record<string, unknown>;
  const str = (v: unknown): string => (typeof v === "string" ? v.trim() : "");
  switch (e.type) {
    case "tool.executed": {
      const tool = str(p.tool);
      const failed = p.ok === false;
      if (!tool) return { label: humanSummary(e.type), detail: failed ? "failed" : "" };
      // The chat's own step phrase ("Read · read_file", "gmail: search
      // messages"), never "Using read_file…".
      return { label: stepPhraseText(stepPhrase(tool)), detail: failed ? "failed" : "" };
    }
    case "agent.state_changed": {
      const from = str(p.from);
      const to = str(p.to ?? p.state);
      const label = stepLabel(e) ?? humanSummary(e.type);
      return { label, detail: from && to ? humanSummary(`agent ${from}→${to}`) : "" };
    }
    case "session.completed": {
      const status = str(p.status);
      return {
        label: humanSummary(e.type),
        detail: status ? statusWords(status, str(p.outcome) || null).word : "",
      };
    }
    case "provider.routed":
    case "provider.failover": {
      const model = str(p.model);
      const prov = str(p.provider);
      const who = [prov ? providerDisplay(prov) : "", model].filter(Boolean).join(" · ");
      return { label: humanSummary(e.type), detail: who };
    }
    default: {
      const label = stepLabel(e) ?? humanSummary(e.type);
      return { label: label || e.type, detail: "" };
    }
  }
}

/* ---- A run a restart cut off (v1.354.0 review) ---------------------------- */

/** The run page's words for a run a restart cut off. */
export const RESTART_WORDS = "Stopped when Iron Jarvis restarted";

/** The summary the boot reconcile writes on such a run when it had none
 *  (agents/orchestrator.reconcile_interrupted_sessions) — the engine's word,
 *  which the receipt must not print under its own plain line. */
export const RESTART_SUMMARY = "interrupted by a daemon restart";

/** The origin a mission's teammate runs under (agents/team.py). */
export const MISSION_MEMBER_ORIGIN = "job:mission-member";

/** The summary the receipt shows. The reconcile's stock words are hidden
 *  while the "Stopped when Iron Jarvis restarted" line stands (it says the
 *  same, in words), and read as that sentence once the offer is answered;
 *  every other summary is shown as it is. */
export function shownSummary(summary: string | null | undefined, interrupted: boolean): string | null {
  const s = (summary ?? "").trim();
  if (!s) return null;
  if (s.toLowerCase() !== RESTART_SUMMARY) return summary ?? null;
  return interrupted ? null : `${RESTART_WORDS}.`;
}

/**
 * Whether the run page offers the restart Continue / Dismiss — exactly the
 * runs the bell offers (GET /sessions/interrupted, routes/sessions.
 * interrupted_rows): a failed run the boot reconcile tagged, NEVER a mission
 * teammate or a delegated child (v1.309.0, contract 7: continuing one started
 * a lone builder with no coordinator to report to; continuing the mission's
 * root re-runs the team). The reconcile tags those too, so the tag alone is
 * not the offer. The bell's 3-day window is its prompt horizon, not a rule
 * about the run, and is not repeated here.
 */
export function offersRestartContinue(
  session: { status?: string | null; interrupted_at?: string | null; origin?: string | null },
  runs: readonly { parent_id?: string | null }[] | null | undefined,
): boolean {
  if ((session.status ?? "").trim().toLowerCase() !== "failed") return false;
  if (!session.interrupted_at) return false;
  if (session.origin === MISSION_MEMBER_ORIGIN) return false;
  return !(runs ?? []).some((r) => !!r.parent_id);
}

/** A row of GET /sessions, as far as finding a continuation needs it. */
export interface ContinuationRow {
  id?: unknown;
  task?: unknown;
  workspace_path?: unknown;
  created_at?: unknown;
}

/**
 * The run a restart Continue just started, read back from the newest rows of
 * GET /sessions — `InterruptedJobActions` (the bell's and the Overview's one
 * Continue, reused here) answers its caller with nothing but "gone", and a
 * continuation is always a NEW run. It is the row that:
 *   - is not this run,
 *   - works in this run's own folder (a continuation reuses the workspace,
 *     and the daemon refuses a second one while one runs there),
 *   - was asked exactly the restart Continue's words (its task starts with
 *     CONTINUE_AFTER_RESTART), and
 *   - was created after the press (a minute of slack for the clocks).
 * Null when none matches (a Dismiss, or an answer that has not landed): the
 * page then stays and re-reads, it never guesses.
 */
export function findContinuation(
  rows: readonly ContinuationRow[] | null | undefined,
  run: { id: string; workspace: string; since: number },
): string | null {
  if (!Array.isArray(rows) || !run.workspace) return null;
  for (const r of rows) {
    const id = typeof r.id === "string" ? r.id : "";
    if (!id || id === run.id) continue;
    if (r.workspace_path !== run.workspace) continue;
    if (typeof r.task !== "string" || !r.task.startsWith(CONTINUE_AFTER_RESTART)) continue;
    const at = typeof r.created_at === "string" ? Date.parse(normalizeIso(r.created_at)) : NaN;
    if (!Number.isFinite(at) || at < run.since - 60_000) continue;
    return id;
  }
  return null;
}
