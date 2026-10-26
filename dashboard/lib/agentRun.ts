/**
 * v1.349.0 (polish wave B, lane B2): the facts behind the helper-agent row.
 *
 * When chat hands work to an agent (an escalation, an @agent with hands, or
 * "Have X do this"), the transcript used to show TWO things: a saved line
 * ("Handing this to the researcher: …") and, while the run lasted, a separate
 * live bubble with its steps. They are ONE row now
 * (components/chat/AgentRunRow): "The researcher is working · Step 2 of 4:
 * draft the summary · 1:24" while it runs, settling to "The researcher worked
 * for 2 min 5 s · 6 steps · Open run" once it is done. The idea comes from
 * 21st.dev agent-elements' subagent row (MIT, ideas only; nothing installed).
 *
 * Nothing here touches React. This file holds:
 *  - the STAMP finalize writes onto the hand-off message when the run ends
 *    (its session, start, end and step count), so the settled row is saved
 *    with the thread and reads the same after a reload or in another window;
 *  - the READER for that stamp, which treats a saved thread as untrusted (an
 *    older version, a hand edit): anything malformed reads as no stamp, and a
 *    message with no stamp renders exactly as it did before this release;
 *  - a tiny registry of the runs a live row is showing right now, so the saved
 *    hand-off line can step aside for the live row without the chat page
 *    handing every message row the page's waiting state.
 */

import { normalizeIso } from "@/lib/format";
import { ROLE_NOUNS } from "@/lib/modelTeam";
import { rowName } from "@/lib/runWords";
import { readSavedPlan, toSavedPlan, type PlanView, type SavedPlan } from "@/lib/planChecklist";

/** v1.352.0 (G16/G19): how a run ENDED, as the session row says it. */
export type AgentRunStatus = "completed" | "failed" | "cancelled";

/** v1.352.0 (G19): the ledger's verdict on a completed run's JOB
 *  (lib/types SessionOutcome); "completed" says nothing more. */
export type AgentRunOutcome = "completed" | "completed_with_failures" | "needs_you";

/** v1.352.0 (G19): one tool's calls in the run, as the ledger counted them
 *  (`tools_used` / `tools_failed` on GET /sessions/{id}/result): `count`
 *  every call, `failed` the ones that failed. lib/runWords says them. */
export interface AgentRunTool {
  tool: string;
  count: number;
  failed: number;
  /** v1.359.0 integrate: a connected app's tool in the daemon's words
   *  ("Gmail › search messages"; the ledger row's `name`). Absent for every
   *  other tool and on older stamps, which read as they did. */
  name?: string;
}

/** At most this many tools are kept on a stamp. The ledger lists a run's
 *  tools by NAME (agents/outcome.session_result sorts `tools_used`), so a run
 *  that used more distinct tools than this keeps the first ones by name; the
 *  card under the reply still lists them all. */
export const STAMP_TOOLS_MAX = 20;

/** What the settled row needs, stamped on the hand-off message by finalize. */
export interface AgentRunStamp {
  /** The agent session that did the work (the row's "Open run" link). */
  session: string;
  /** When the run started and ended, in ms since the epoch: the SESSION's own
   *  `created_at` / `finished_at`, one clock for both ends, so the duration is
   *  the daemon's and not this window's. Null when not known. */
  startedAt: number | null;
  endedAt: number | null;
  /** How many steps the agent took: the ledger's count (`steps` on
   *  GET /sessions/{id}/result), the same number the run card under the reply
   *  shows, so the two never disagree. Null when not known. */
  steps: number | null;
  /** v1.350.0 (lane C1): the plan the run worked through, as it ended (goal
   *  + state per step, at most 20 steps of 200 characters; lib/planChecklist
   *  SavedPlan). Absent for a run that made no plan, and on every stamp from
   *  before v1.350.0, which read exactly as they did. */
  plan?: SavedPlan;
  // v1.352.0 (G16, G19): the receipt's words, each ONLY when known, so a
  // stamp from before this release keeps its old shape and reads as it did.
  /** How the run ended (the session row's status): the settled row says
   *  "· stopped" for a cancelled run and "· failed" for a failed one. */
  status?: AgentRunStatus;
  /** The ledger's verdict on a completed run: "· needs you" / "· part failed". */
  outcome?: AgentRunOutcome;
  /** The run ended at its step limit (a failed run whose summary says so):
   *  "· hit its step limit", never a bare "failed". */
  stepLimit?: true;
  /** The provider and model that SERVED the run (the session row's pair). */
  provider?: string;
  model?: string;
  /** One sentence when the run's model failed over or was refused. */
  routeNote?: string;
  /** The folder the run worked in (its workspace), for "Open folder". */
  workspacePath?: string;
  /** What it did, counted per tool (the ledger's own numbers). */
  tools?: AgentRunTool[];
  /** v1.352.0 integrate (the wave-1 receipt, G18): WHO ran it — the
   *  receipt's `agent_name` (a roster name: "custom:researcher"), else its
   *  `agent_type` ("builder"). Names the settled row when the hand-off line
   *  named nobody (the default agent). */
  agent?: string;
  /** v1.352.0 integrate (G21 on the helper agent): did the run's model stay
   *  on the user's network (the receipt's `on_this_network`)? Only a real
   *  boolean; absent = not said, and the row says nothing about where. */
  onThisNetwork?: boolean;
}

/** The fields of a chat message this file reads; the page's ChatMessage
 *  satisfies it, and so does a raw line off GET /chat/threads/{id}. */
export interface HandOffLike {
  escalated?: string;
  escalatedTo?: string;
  awaitingSession?: string;
  /** v1.352.0 (G19): the remote agent the chat picked for this hand-off,
   *  when its pick could not be honoured here (a remote has no local run):
   *  the line says the work runs on this PC and how to reach that agent. */
  droppedRemote?: string;
}

/** The key the stamp is saved under on the hand-off message. */
export const AGENT_RUN_KEY = "agentRun";

/** A session id fit for a link: one path segment of plain id characters. A
 *  saved thread is untrusted, so anything else reads as no session. */
const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;

/** A daemon timestamp (naive UTC ISO, the daemon's own shape) in ms since the
 *  epoch, or null. A zone-less stamp is read as UTC (lib/format normalizeIso):
 *  read as local time, a run in a UTC-offset zone would be hours long. */
export function stampMs(iso: unknown): number | null {
  if (typeof iso !== "string" || !iso.trim()) return null;
  const t = Date.parse(normalizeIso(iso.trim()));
  return Number.isFinite(t) ? t : null;
}

function epochOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
}

function countOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null;
}

/* ------------------------------------------- v1.352.0: the receipt's words */

const STATUSES: readonly AgentRunStatus[] = ["completed", "failed", "cancelled"];
const OUTCOMES: readonly AgentRunOutcome[] = ["completed", "completed_with_failures", "needs_you"];

function oneOf<T extends string>(v: unknown, allowed: readonly T[]): T | null {
  const s = typeof v === "string" ? v.trim().toLowerCase() : "";
  return (allowed as readonly string[]).includes(s) ? (s as T) : null;
}

/** One line of plain text, cut to `max` characters, or "" (a saved thread is
 *  untrusted: control characters out, whitespace collapsed). */
function line(v: unknown, max: number): string {
  if (typeof v !== "string") return "";
  const flat = v.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** A tool id fit to keep: one token of id characters (a connected app's
 *  `mcp__<app>__<tool>` included). */
const TOOL_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;

/** A folder path fit to keep: one line, at most 1000 characters, no control
 *  characters. Read back, never trusted further than "a path to offer". */
function folderOrEmpty(v: unknown): string {
  if (typeof v !== "string") return "";
  const p = v.trim();
  return p && p.length <= 1000 && !/[\u0000-\u001f\u007f]/.test(p) ? p : "";
}

/**
 * The ledger's per-tool counts (`tools_used` + `tools_failed`, or a saved
 * stamp's `tools`), checked entry by entry: at most STAMP_TOOLS_MAX tools,
 * each a real id with whole counts and no more failures than calls.
 */
export function stampTools(
  used: unknown,
  failed?: unknown,
): AgentRunTool[] {
  const bad = new Map<string, number>();
  for (const f of Array.isArray(failed) ? failed : []) {
    const r = f as { tool?: unknown; count?: unknown } | null;
    if (r && typeof r.tool === "string" && TOOL_ID.test(r.tool)) {
      const n = countOrNull(r.count);
      if (n !== null) bad.set(r.tool, n);
    }
  }
  const out: AgentRunTool[] = [];
  const seen = new Set<string>();
  for (const u of Array.isArray(used) ? used : []) {
    if (out.length >= STAMP_TOOLS_MAX) break;
    const r = u as { tool?: unknown; count?: unknown; failed?: unknown; name?: unknown } | null;
    if (!r || typeof r.tool !== "string" || !TOOL_ID.test(r.tool) || seen.has(r.tool)) continue;
    const count = countOrNull(r.count);
    if (count === null || count === 0) continue;
    seen.add(r.tool);
    // A saved stamp carries `failed` on the entry; a ledger result carries
    // a separate `tools_failed` list.
    const nBad = countOrNull(r.failed) ?? bad.get(r.tool) ?? 0;
    const name = rowName(r.name);
    out.push({ tool: r.tool, count, failed: Math.min(nBad, count), ...(name ? { name } : {}) });
  }
  return out;
}

/** The phrase a failed run's summary starts with when it ran out of steps
 *  (agents/runtime.py: "stopped: reached max steps before completion"). */
const STEP_LIMIT = /^stopped:\s*reached max steps/i;

/** True when a run's summary says it stopped at its step limit. */
export function atStepLimit(summary: unknown): boolean {
  return typeof summary === "string" && STEP_LIMIT.test(summary.trim());
}

/** What finalize read about the run's END, beyond its times and step count:
 *  the session row (status, outcome, the served pair, its folder, its
 *  summary) and the ledger result (route note, per-tool counts). */
export interface RunEndFacts {
  status?: unknown;
  outcome?: unknown;
  summary?: unknown;
  provider?: unknown;
  model?: unknown;
  workspacePath?: unknown;
  routeNote?: unknown;
  toolsUsed?: unknown;
  toolsFailed?: unknown;
  /** The receipt's who (agent_name, else agent_type) and where. */
  agent?: unknown;
  onThisNetwork?: unknown;
}

/** A roster name fit to keep ("builder", "custom:researcher",
 *  "remote:hermes"): one short token of name characters. */
const AGENT_NAME = /^[A-Za-z0-9][A-Za-z0-9 _.:-]{0,79}$/;

/** The v1.352.0 fields of a stamp, each only when known (an old stamp, or a
 *  finalize with nothing to say, keeps the old shape exactly). */
function endFields(f: RunEndFacts | null | undefined): Partial<AgentRunStamp> {
  if (!f) return {};
  const out: Partial<AgentRunStamp> = {};
  const status = oneOf(f.status, STATUSES);
  if (status) out.status = status;
  const outcome = oneOf(f.outcome, OUTCOMES);
  if (outcome && outcome !== "completed") out.outcome = outcome;
  if (status === "failed" && atStepLimit(f.summary)) out.stepLimit = true;
  const provider = line(f.provider, 80);
  if (provider) out.provider = provider;
  const model = line(f.model, 160);
  if (model) out.model = model;
  const routeNote = line(f.routeNote, 400);
  if (routeNote) out.routeNote = routeNote;
  const workspacePath = folderOrEmpty(f.workspacePath);
  if (workspacePath) out.workspacePath = workspacePath;
  const tools = stampTools(f.toolsUsed, f.toolsFailed);
  if (tools.length) out.tools = tools;
  const agent = typeof f.agent === "string" ? f.agent.trim() : "";
  if (agent && AGENT_NAME.test(agent)) out.agent = agent;
  if (typeof f.onThisNetwork === "boolean") out.onThisNetwork = f.onThisNetwork;
  return out;
}

/** Build the stamp from what finalize read: the session row's times and the
 *  ledger's step count. Junk in any field becomes null, never a guess.
 *  v1.352.0 (G19): plus how the run ended and what it did (`end`). */
export function makeAgentRunStamp(input: {
  session: string;
  createdAt?: unknown;
  finishedAt?: unknown;
  steps?: unknown;
  /** The run's checklist as it ended (settled), or nothing for no plan. */
  plan?: PlanView | null;
  end?: RunEndFacts | null;
}): AgentRunStamp {
  const startedAt = stampMs(input.createdAt);
  let endedAt = stampMs(input.finishedAt);
  // An end before the start is a clock oddity: the length is then unknown,
  // and an unknown length is said as nothing rather than as a wrong one.
  if (startedAt !== null && endedAt !== null && endedAt < startedAt) endedAt = null;
  const plan = toSavedPlan(input.plan);
  return {
    session: input.session,
    startedAt,
    endedAt,
    steps: countOrNull(input.steps),
    // Only when there is one: a stamp with no plan keeps its old shape.
    ...(plan ? { plan } : {}),
    ...endFields(input.end),
  };
}

/** The stamp saved on a message, checked field by field, or null when there
 *  is none (a message from before v1.349.0) or it is not one. */
export function readAgentRun(m: unknown): AgentRunStamp | null {
  if (!m || typeof m !== "object") return null;
  const raw = (m as Record<string, unknown>)[AGENT_RUN_KEY];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.session !== "string" || !SESSION_ID.test(r.session)) return null;
  const startedAt = epochOrNull(r.startedAt);
  let endedAt = epochOrNull(r.endedAt);
  if (startedAt !== null && endedAt !== null && endedAt < startedAt) endedAt = null;
  // v1.350.0: the saved plan, read as untrusted as the rest; a malformed one
  // is no plan, and the stamp around it still reads.
  const plan = toSavedPlan(readSavedPlan(r.plan));
  // v1.352.0: the receipt's words, read as untrusted as the rest (the same
  // checks the stamp was made with). `stepLimit` is kept only on a failed run.
  const end = endFields({
    status: r.status,
    outcome: r.outcome,
    provider: r.provider,
    model: r.model,
    routeNote: r.routeNote,
    workspacePath: r.workspacePath,
    toolsUsed: r.tools,
    agent: r.agent,
    onThisNetwork: r.onThisNetwork,
  });
  if (r.stepLimit === true && end.status === "failed") end.stepLimit = true;
  return {
    session: r.session,
    startedAt,
    endedAt,
    steps: countOrNull(r.steps),
    ...(plan ? { plan } : {}),
    ...end,
  };
}

/** How long the run took in ms, or null when either end is unknown. */
export function runDurationMs(run: AgentRunStamp): number | null {
  if (run.startedAt === null || run.endedAt === null) return null;
  return run.endedAt >= run.startedAt ? run.endedAt - run.startedAt : null;
}

/** The hand-off message waiting on `session` (newest first), or null: the
 *  message whose saved line the live row takes the place of. */
export function handOffFor<M extends HandOffLike>(
  msgs: readonly M[],
  session: string | null | undefined,
): M | null {
  if (!session) return null;
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m && m.escalated && m.awaitingSession === session) return m;
  }
  return null;
}

/** Stamp the hand-off message waiting on `session` with how the run went.
 *  Returns the SAME array when no message waits on it (a plain agent turn
 *  with no hand-off line, or a thread that never saved one), so a caller can
 *  tell "nothing to settle" by identity. Every other message keeps its
 *  object, so reference checks downstream (the save rebase) still hold. */
export function stampHandOff<M extends HandOffLike>(
  msgs: M[],
  session: string,
  stamp: AgentRunStamp,
): M[] {
  const target = handOffFor(msgs, session);
  if (!target) return msgs;
  return msgs.map((m) => (m === target ? { ...m, [AGENT_RUN_KEY]: stamp } : m));
}

/** The session row fields finalize reads (GET /sessions/{id}, SessionView). */
export interface RunRowLike {
  created_at?: unknown;
  finished_at?: unknown;
  status?: unknown;
  outcome?: unknown;
  summary?: unknown;
  provider?: unknown;
  model?: unknown;
  workspace_path?: unknown;
}

/** The ledger result fields finalize reads (GET /sessions/{id}/result, or the
 *  daemon line's `receipt`: components/chat/RunResultCard RunResult). */
export interface RunResultLike {
  steps?: unknown;
  provider?: unknown;
  model?: unknown;
  route_note?: unknown;
  tools_used?: unknown;
  tools_failed?: unknown;
  /** The ledger's own reading of how the run ended (stands in for a row
   *  that holds none — the row is read first). */
  status?: unknown;
  outcome?: unknown;
  /** The wave-1 receipt's who and where (G18). */
  agent_name?: unknown;
  agent_type?: unknown;
  on_this_network?: unknown;
}

/** finalize's one call (both of its paths, the composed reply and the
 *  daemon's appended line): stamp the hand-off waiting on `session` from the
 *  session row it read (`created_at`, `finished_at`) and the ledger's step
 *  count, plus (v1.350.0) the run's settled plan checklist when it made one,
 *  plus (v1.352.0, G19) how it ended and what it did: the row's status,
 *  outcome, served model and folder, the result's route note and per-tool
 *  counts (`result`, when the ledger read answered). The SAME array back when
 *  nothing waits on it (see stampHandOff). */
export function settleHandOff<M extends HandOffLike>(
  msgs: M[],
  session: string,
  row: RunRowLike | null | undefined,
  steps?: unknown,
  plan?: PlanView | null,
  result?: RunResultLike | null,
): M[] {
  return stampHandOff(
    msgs,
    session,
    makeAgentRunStamp({
      session,
      createdAt: row?.created_at,
      finishedAt: row?.finished_at,
      steps,
      plan,
      end: {
        // The row's own verdict first; the ledger's (the same derivation the
        // result card reads) when the row holds none.
        status: row?.status || result?.status,
        outcome: row?.outcome || result?.outcome,
        summary: row?.summary,
        // The row holds the SERVED pair once a run ends; the result carries
        // the same two (G18), which stand in for a row that lacks them.
        provider: row?.provider || result?.provider,
        model: row?.model || result?.model,
        workspacePath: row?.workspace_path,
        routeNote: result?.route_note,
        toolsUsed: result?.tools_used,
        toolsFailed: result?.tools_failed,
        // v1.352.0 integrate: who ran it and where (the receipt read).
        agent: result?.agent_name || result?.agent_type,
        onThisNetwork: result?.on_this_network,
      },
    }),
  );
}

/**
 * v1.352.0 (G16): the user pressed Stop on the run. There is no finished
 * session row to read yet (the cancel is still on its way), so the hand-off
 * settles from what this window knows: the run's start (the session's own
 * `created_at`, which the live row's clock already counted from) and now.
 * Status "cancelled", so the row reads "The researcher worked for 45 s ·
 * stopped" (or "… was stopped" when the start was never read) in place of
 * the line that still said it was handing the work over. The SAME array back
 * when nothing waits on `session`.
 */
export function stopHandOff<M extends HandOffLike>(
  msgs: M[],
  session: string,
  startedAt: number | null | undefined,
  now: number = Date.now(),
): M[] {
  const start = epochOrNull(startedAt);
  const end = epochOrNull(now);
  return stampHandOff(msgs, session, {
    session,
    startedAt: start,
    endedAt: start !== null && end !== null && end >= start ? end : null,
    steps: null,
    status: "cancelled",
  });
}

/** v1.352.0 (G19): a remote agent's name, fit for a sentence ("hermes-mac"),
 *  or "" when it is not one. */
export function remoteName(raw: unknown): string {
  const s = typeof raw === "string" ? raw.trim() : "";
  const name = s.toLowerCase().startsWith("remote:") ? s.slice("remote:".length).trim() : s;
  return /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(name) ? name : "";
}

/** The sentence the saved hand-off line says (unchanged since v1.139.0): the
 *  named specialist when one was chosen, else the plain hand-off. ONE source,
 *  for the line itself and for the row that settles it.
 *  v1.352.0 (G19): when the chat picked a REMOTE agent this page cannot run a
 *  session for, the line says so instead of letting it run here silently:
 *  "… Running on this PC. Reach hermes with @hermes." */
export function handOffSentence(
  escalated: string,
  escalatedTo?: string | null,
  droppedRemote?: string | null,
): string {
  const said = escalatedTo
    ? `Handing this to ${escalatedTo}: ${escalated}.`
    : `Taking this on properly: ${escalated}.`;
  const remote = remoteName(droppedRemote);
  return remote ? `${said} Running on this PC. Reach ${remote} with @${remote}.` : said;
}

/**
 * v1.352.0 (G16, G19): how the run ended, as the settled row's last words:
 * "stopped", "failed", "hit its step limit", "needs you", "part failed", or
 * null for a clean finish (and for every stamp from before this release).
 */
export function runEndWords(run: Pick<AgentRunStamp, "status" | "outcome" | "stepLimit">): string | null {
  if (run.status === "cancelled") return "stopped";
  if (run.status === "failed") return run.stepLimit ? "hit its step limit" : "failed";
  if (run.outcome === "needs_you") return "needs you";
  if (run.outcome === "completed_with_failures") return "part failed";
  return null;
}

/** How a roster name reads in a sentence (moved here from the chat page in
 *  v1.352.0 so the settled row can name the agent the receipt says ran):
 *  "custom:researcher" -> "your researcher agent", "remote:hermes" -> "the
 *  hermes remote agent", "builder" -> "the builder". */
export function agentPhrase(name: string): string {
  // v1.395.0 (the long-jobs plan's Phase 6): a helper the planner asked for
  // by ROLE ("role:code") is named by its role until the team router picks a
  // member — "a team member for coding", never "the role:code".
  if (name.startsWith("role:")) {
    const noun = ROLE_NOUNS[name.slice(5).trim().toLowerCase()];
    return noun ? `a team member for ${noun}` : "a team member";
  }
  const custom = name.startsWith("custom:");
  const remote = name.startsWith("remote:");
  const bare = (custom || remote ? name.slice(name.indexOf(":") + 1) : name).trim();
  // A degenerate name (empty slug etc.) can only arrive on a broken wire —
  // degrade to a sane phrase, never "your  agent" / "the undefined".
  if (!bare) return "a specialist agent";
  if (custom) return `your ${bare} agent`;
  if (remote) return `the ${bare} remote agent`;
  return `the ${bare}`;
}

/** The settled row's subject: the hand-off line's own words when it named
 *  the agent, else (v1.352.0 integrate) the agent the receipt says RAN —
 *  "The builder worked for 45 s" where it used to read "The agent". */
export function settledRunName(escalatedTo: string | null | undefined, run: AgentRunStamp): string {
  const said = typeof escalatedTo === "string" ? escalatedTo.trim() : "";
  return agentRunName(said || (run.agent ? agentPhrase(run.agent) : ""));
}

/** The row's subject, at the start of a sentence: "the researcher" reads
 *  "The researcher", "your invoice-chaser agent" reads "Your invoice-chaser
 *  agent". An unnamed hand-off (the default agent) is "The agent". */
export function agentRunName(escalatedTo?: string | null): string {
  const flat = typeof escalatedTo === "string" ? escalatedTo.replace(/\s+/g, " ").trim() : "";
  if (!flat) return "The agent";
  const clipped = flat.length > 60 ? `${flat.slice(0, 59)}…` : flat;
  return clipped.charAt(0).toUpperCase() + clipped.slice(1);
}

/* ------------------------------------------------------- the live registry */

// The sessions a live row is on screen for, counted (two rows for one run
// must both unmount before the line comes back). A module store, not React
// state: the saved line asks it directly, so the chat page never hands every
// message row its waiting state (the v1.250.0 S-05 rule, one memoized row).
const live = new Map<string, number>();
const listeners = new Set<() => void>();

function emit(): void {
  for (const cb of [...listeners]) cb();
}

export const liveRuns = {
  /** A live row for `session` is on screen. Returns its removal. */
  add(session: string): () => void {
    live.set(session, (live.get(session) ?? 0) + 1);
    emit();
    let done = false;
    return () => {
      if (done) return;
      done = true;
      const n = (live.get(session) ?? 0) - 1;
      if (n > 0) live.set(session, n);
      else live.delete(session);
      emit();
    };
  },
  /** Is a live row on screen for `session` right now? */
  has(session: string | null | undefined): boolean {
    return !!session && (live.get(session) ?? 0) > 0;
  },
  subscribe(cb: () => void): () => void {
    listeners.add(cb);
    return () => {
      listeners.delete(cb);
    };
  },
};
