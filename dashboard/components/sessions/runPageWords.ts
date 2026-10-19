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

/**
 * The run's title: the FIRST non-blank line of what it was asked, as plain
 * text (a model or a person may write markdown), clipped. `full` is the whole
 * task as one plain line when the title had to drop anything (more lines, or
 * a clip) — the header shows it on demand — else null.
 */
export function runTitle(task: string | null | undefined): { title: string; full: string | null } {
  const raw = (task ?? "").replace(/\r\n/g, "\n");
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
