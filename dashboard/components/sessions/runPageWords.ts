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
 *   local / cloud     components/chat/TurnReceipt.replyWhere (the chat
 *                     receipt's own words and rule, v1.360.1)
 */

import type { IJEvent } from "@/lib/types";
import { plainText } from "@/components/Markdown";
import { stepLabel } from "@/components/chat/stepLabel";
import { replyWhere, type ReplyWhere } from "@/components/chat/TurnReceipt";
import { humanSummary } from "@/lib/eventWords";
import { stepPhrase, stepPhraseText } from "@/lib/stepWords";
import { statusWords } from "@/lib/statusWords";
import { isServerAlias, modelRowName, providerDisplay } from "@/lib/onboarding";
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

const ORIGINAL_TASK_MARK = " Original task: ";

/** The earlier run's task out of the continue door's recap. The daemon
 *  writes it with Python's `repr` (`{prev.task!r}`, agents/orchestrator.
 *  continue_session): quoted, with newlines, tabs, backslashes and the quote
 *  escaped. "" when the recap carries none. */
export function originalTaskOf(recap: string): string {
  const at = recap.indexOf(ORIGINAL_TASK_MARK);
  if (at < 0) return "";
  const q = recap.charAt(at + ORIGINAL_TASK_MARK.length);
  if (q !== "'" && q !== '"') return "";
  const from = at + ORIGINAL_TASK_MARK.length + 1;
  const end = recap.indexOf(`${q}. Prior result: `, from);
  if (end < 0) return "";
  return recap
    .slice(from, end)
    .replace(/\\(\\|n|t|r|'|")/g, (_m, c: string) =>
      c === "n" ? "\n" : c === "t" ? "\t" : c === "r" ? "" : c,
    );
}

/** What the user asked a run, without the wrapping the chat and the continue
 *  door add around it (above). Unwrapped text is returned as it is.
 *  v1.354.0 integrate: a restart Continue's run was asked the APP's words
 *  ("Continue where you left off — Iron Jarvis restarted…"), so it is titled
 *  by the run it continues — the earlier task in the door's recap — the way
 *  the mission screen keeps a resumed mission's objective. */
export function askedText(task: string | null | undefined, depth = 0): string {
  let t = (task ?? "").replace(/\r\n/g, "\n");
  if (t.startsWith(CHAT_RECAP_HEAD)) {
    const at = t.lastIndexOf(CHAT_RECAP_SEP);
    if (at >= 0) t = t.slice(at + CHAT_RECAP_SEP.length);
  }
  t = t.replace(SKILL_LINE_RE, "");
  const cont = t.indexOf(CONTINUE_RECAP_MARK);
  if (cont > 0) {
    const asked = t.slice(0, cont);
    if (asked.trim() === CONTINUE_AFTER_RESTART && depth < 4) {
      const earlier = originalTaskOf(t.slice(cont));
      if (earlier.trim()) return askedText(earlier, depth + 1);
    }
    t = asked;
  }
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
  // v1.360.0 integrate (the final re-audit's regression 8): the "fleet"
  // alias IS its place — the machine's name ("DGX Spark") or "Your model
  // server", the model menu's own words — said once, never "DGX Spark ·
  // fleet" beside a reply that says "answered by DGX Spark".
  if (isServerAlias(m)) return modelRowName({ provider: p, model: m }, catalog);
  const endpoint = p.startsWith("fleet-") || p === "custom";
  let where = endpoint ? endpointLabel(p, catalog) : providerDisplay(p);
  // A row that names its MODEL in `name` is not a place.
  if (!where || where === m) where = providerDisplay(p);
  return m ? `${where} · ${m}` : where;
}

/** A GET /models row as far as {@link runWhere} reads it. */
export interface RunWhereRow extends RanOnRow {
  /** `egress.stays_on_this_network` for the row's provider (v1.352.0). */
  on_this_network?: boolean;
}

/** The slice of the run's own receipt (GET /sessions/{id}/result) that
 *  {@link runWhere} reads: the provider the flag was judged for, and the
 *  flag (null when the daemon had no provider to judge). */
export interface RunWhereReceipt {
  provider?: unknown;
  on_this_network?: unknown;
}

/**
 * v1.360.1 (G21, the last remainder): WHERE a run ran, in the chat receipt's
 * own words and by its own rule (TurnReceipt.replyWhere: "local" or "cloud",
 * nothing when it is not known, never for the demo model). There is one word
 * table, replyWhere's; this only finds the flag to hand it:
 *
 *   1. the run's own receipt (`on_this_network`, egress.stays_on_this_network
 *      for the run's provider), when it was judged for THIS provider (a
 *      receipt read before the run's served pair was written names the old
 *      one, and is not used);
 *   2. else the GET /models row for the run's provider + model (the same
 *      predicate, the daemon's one answer per provider).
 *
 * Never the row's `kind`: a routing class is not where the run went (a
 * "local" endpoint the user pointed at a cloud server says false), so a row
 * without the flag says nothing. No endpoint label is passed: the Model line
 * already names the machine, and the word is said beside it.
 */
export function runWhere(
  provider: string | null | undefined,
  model: string | null | undefined,
  catalog: readonly RunWhereRow[] | null | undefined,
  receipt?: RunWhereReceipt | null,
): ReplyWhere | null {
  const p = (provider ?? "").trim();
  const m = (model ?? "").trim();
  if (!p) return null;
  let here: boolean | undefined;
  if (
    receipt &&
    typeof receipt.on_this_network === "boolean" &&
    typeof receipt.provider === "string" &&
    receipt.provider.trim() === p
  ) {
    here = receipt.on_this_network;
  } else {
    const row = (Array.isArray(catalog) ? catalog : []).find(
      (r) => !!r && r.provider === p && r.model === m,
    );
    if (row && typeof row.on_this_network === "boolean") here = row.on_this_network;
  }
  return replyWhere({ provider: p, model: m, on_this_network: here });
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
 *  (agents/orchestrator.RESTART_SUMMARY, v1.354.0 integrate): the run
 *  page's own sentence, so the chat line a chat-started run delivers home,
 *  the bell and this page say ONE thing — which the receipt must still not
 *  print twice under its own plain line. */
export const RESTART_SUMMARY = `${RESTART_WORDS}.`;

/** What the reconcile wrote before v1.354.0 — stored rows and saved chat
 *  lines keep it, so the receipt still reads it as the restart line. */
export const OLD_RESTART_SUMMARY = "interrupted by a daemon restart";

/** Whether *summary* is the reconcile's stock restart words (either era). */
export function isRestartSummary(summary: string | null | undefined): boolean {
  const s = (summary ?? "").trim().toLowerCase();
  return s === RESTART_SUMMARY.toLowerCase() || s === OLD_RESTART_SUMMARY;
}

/** The origin a mission's teammate runs under (agents/team.py). */
export const MISSION_MEMBER_ORIGIN = "job:mission-member";

/* ---- A run that was stopped (v1.359.0 integrate, lane 9A review) --------- */

/** The shared stopped sentences a stopped run's summary reads since v1.359.0
 *  (agents/orchestrator.STOPPED_SUMMARY / STOPPED_BEFORE_START, which ARE
 *  agents/chat_delivery.STOPPED_SENTENCE / STOPPED_BEFORE_START_SENTENCE; the
 *  chat page's own words for a stop). Pinned equal from Python. */
export const STOPPED_SUMMARY = "The agent was stopped before it finished.";
export const STOPPED_BEFORE_START = "The agent was stopped before it started.";

/** What the engine wrote on a stopped run before v1.359.0 (the session and
 *  "cancelled" were the engine's words). Rows on disk keep them, so the run
 *  page reads them as the sentences above — like the old restart words. */
export const OLD_STOPPED_SUMMARIES: Readonly<Record<string, string>> = {
  "session cancelled by the user.": STOPPED_SUMMARY,
  "cancelled while queued (never started).": STOPPED_BEFORE_START,
};

/** The summary the receipt shows. The reconcile's stock words are hidden
 *  while the "Stopped when Iron Jarvis restarted" line stands (it says the
 *  same, in words), and read as that sentence once the offer is answered;
 *  an older stopped run's engine words read as the shared stopped sentence;
 *  every other summary is shown as it is. */
export function shownSummary(summary: string | null | undefined, interrupted: boolean): string | null {
  const s = (summary ?? "").trim();
  if (!s) return null;
  const old = OLD_STOPPED_SUMMARIES[s.toLowerCase()];
  if (old) return old;
  if (!isRestartSummary(s)) return summary ?? null;
  return interrupted ? null : RESTART_SUMMARY;
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
