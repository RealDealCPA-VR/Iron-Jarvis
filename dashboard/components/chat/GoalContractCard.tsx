"use client";

// Goal birth (v1.208.0) — goals are BORN IN CONTEXT, never configured from a
// blank panel. This file is the whole surface: the "Keep doing this?" chip
// that appears on a completed chat turn which did time-shaped or
// recurring-looking work (the v1.120.0 "Keep this as a workflow?" sibling),
// and the GoalContractCard it opens — a PRE-FILLED review of one standing
// goal, deterministically derived from the turn's own user message, ending in
// one decision: Create.
//
// THE BAR for the chip (deliberately high): a chip that fires on trivial Q&A
// trains the user to ignore EVERY chip, so the heuristic is deterministic,
// narrow, and honest — explicit recurring vocabulary in the USER'S OWN words
// (every/daily/nightly/each week/watch/monitor/keep doing…), OR a turn whose
// tool loop actually ran workflow/schedule tools. No model call, no guessing
// from the reply text, no "this looks automatable" vibes. "what is 2+2"
// must never grow a chip.
//
// Everything the card pre-fills is a DETERMINISTIC template over the user's
// message — never model-written. The wire contract (backend lands in
// parallel; the local types below are this file's own):
//   POST /goals {name, contract_text, project_id?, schedule?,
//                budget:{max_dollars…}, verifier:{kind:"manual"}} -> {goal}
// The deny floor (shell/repl/browser/web_action/mcp_call) is unrepresentable
// in this body ON PURPOSE and refused server-side — the card grants NOTHING;
// asks flow to the bell/phone like any run.

import { useState } from "react";
import Link from "next/link";
import { Check, DoorOpen, Loader2, Target, X } from "lucide-react";

import { ApiError, post } from "@/lib/api";

/* ------------------------------------------------------------------------ */
/* The heuristic (exported so the test can pin the bar)                      */
/* ------------------------------------------------------------------------ */

/** Recurring vocabulary in the USER'S message — time-shaped words only.
 *  Word-bounded so "everyone"/"watched" never match. */
const RECURRING_VOCAB =
  /\b(every|daily|nightly|weekly|hourly|monthly|whenever|recurring)\b|\beach\s+(day|week|month|morning|night|evening|hour|time)\b|\bkeep\s+(doing|watching|checking|monitoring|tracking|an\s+eye\s+on|track\s+of)\b|\b(watch|monitor)\b/i;

/** Tools whose execution marks a turn as time-shaped work (workflow ran,
 *  schedule touched) — matched on the tool NAME the daemon reported. */
const TIME_SHAPED_TOOL = /workflow|schedule/i;

/** Should this completed turn offer "Keep doing this? → Make it a goal"?
 *  True iff the turn's USER message carries recurring vocabulary OR the turn
 *  actually ran workflow/schedule tools. Deliberately narrow — see the bar
 *  in the file header. */
export function shouldOfferGoal(
  userText: string,
  toolsUsed?: string[] | null,
): boolean {
  const text = (userText || "").trim();
  if (RECURRING_VOCAB.test(text)) return true;
  return (toolsUsed ?? []).some((t) => TIME_SHAPED_TOOL.test(t));
}

/* ------------------------------------------------------------------------ */
/* Deterministic pre-fill (exported so the test can pin each derivation)     */
/* ------------------------------------------------------------------------ */

/** Suggest a cron preset from the vocabulary — "daily"→9am, "nightly"→9pm,
 *  etc. "" means MANUAL (no schedule sent; the user runs the goal). Night is
 *  checked before day so "every night" never lands on the morning preset. */
export function suggestSchedule(userText: string): string {
  const t = userText || "";
  if (/\bnightly\b|\b(every|each)\s+(night|evening)\b/i.test(t))
    return "0 21 * * *";
  if (/\bdaily\b|\b(every|each)\s+(day|morning)\b/i.test(t))
    return "0 9 * * *";
  if (/\bhourly\b|\b(every|each)\s+hour\b/i.test(t)) return "0 * * * *";
  if (/\bweekly\b|\b(every|each)\s+week\b/i.test(t)) return "0 9 * * 1";
  if (/\bmonthly\b|\b(every|each)\s+month\b/i.test(t)) return "0 9 1 * *";
  return "";
}

/** Lead-in words that carry scheduling/politeness, not identity — stripped
 *  from the front of the user message when deriving the short name. */
const NAME_LEAD_STOP = new Set([
  "please",
  "can",
  "you",
  "could",
  "would",
  "hey",
  "every",
  "each",
  "daily",
  "nightly",
  "weekly",
  "hourly",
  "monthly",
  "day",
  "morning",
  "night",
  "evening",
  "week",
  "month",
  "hour",
  "time",
  "at",
  "on",
  "and",
  "then",
]);

/** A short goal name from the user message: drop the scheduling lead-in,
 *  keep the next six words. Deterministic — never model-written. */
export function goalNameFrom(userText: string): string {
  const words = (userText || "").trim().replace(/\s+/g, " ").split(" ");
  let start = 0;
  while (
    start < words.length &&
    NAME_LEAD_STOP.has(words[start].toLowerCase().replace(/[^a-z0-9]/g, ""))
  )
    start += 1;
  const core = words.slice(start, start + 6).join(" ");
  const name = (core || words.slice(0, 6).join(" "))
    .replace(/[.?!,;:]+$/, "")
    .trim();
  return (name || "standing goal").slice(0, 60);
}

/** The user message reframed as a standing goal — a FIXED template quoting
 *  the ask verbatim, so the contract is the user's words, not a model's. */
export function goalContractFrom(userText: string): string {
  const ask = (userText || "").trim().replace(/\s+/g, " ");
  return (
    "Standing goal, born from a chat turn.\n\n" +
    `The ask, in your words: "${ask}"\n\n` +
    "Each run: do this again for the current period, stay inside the " +
    "budget, ask before anything that needs a new grant, and leave a " +
    "reviewable record of what changed."
  );
}

/** The plain-words guarantees, stated AT the Create button — pinned verbatim
 *  by the test so no rewrite can quietly soften them. */
export const GOAL_GUARANTEES =
  "Deny-floor tools can never be granted. The budget is checked before " +
  "every run — a run already in progress finishes and is counted. " +
  "Everything is logged and undoable. Stop always works.";

/** The ONE visible budget default — tight on purpose: a goal's first budget
 *  should be small enough that a runaway run is a nuisance, not a bill. */
export const GOAL_DEFAULT_BUDGET_DOLLARS = 2;

/* ------------------------------------------------------------------------ */
/* The card                                                                  */
/* ------------------------------------------------------------------------ */

export function GoalContractCard({
  userText,
  projectId,
  onDismiss,
}: {
  /** The turn's USER message — the context the goal is born from. */
  userText: string;
  /** The chat's grounded project, carried onto the goal when present. */
  projectId?: string | null;
  /** "Not now" — the caller hides the whole offer for this turn. */
  onDismiss?: () => void;
}) {
  const [name, setName] = useState(() => goalNameFrom(userText));
  const [contract, setContract] = useState(() => goalContractFrom(userText));
  const [schedule, setSchedule] = useState(() => suggestSchedule(userText));
  const [budget, setBudget] = useState(String(GOAL_DEFAULT_BUDGET_DOLLARS));
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const budgetNum = Number(budget);
  const budgetOk = Number.isFinite(budgetNum) && budgetNum > 0;

  async function create() {
    if (creating || created) return;
    setCreating(true);
    setError(null);
    try {
      // The exact wire body — NOTHING granted (`allowed_grants` deliberately
      // absent: asks flow to notifications like any run), verifier manual,
      // budget a single dollar cap checked before every run.
      const body: Record<string, unknown> = {
        name: name.trim(),
        contract_text: contract.trim(),
        budget: { max_dollars: budgetNum },
        verifier: { kind: "manual" },
      };
      if (schedule.trim()) body.schedule = schedule.trim();
      if (projectId) body.project_id = projectId;
      await post<{ goal: unknown }>("/goals", body);
      setCreated(true);
    } catch (e) {
      // Errors verbatim — the daemon's sentence, not a paraphrase.
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div
      data-testid="goal-contract-card"
      className="ml-11 mt-2 max-w-[640px] rounded-xl border border-accent/20 bg-accent/[0.04]"
    >
      <div className="flex items-start gap-2.5 border-b border-white/[0.05] px-3.5 py-2.5">
        <Target size={15} className="mt-0.5 shrink-0 text-accent-soft" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-mono text-[13px] text-zinc-100">
              standing goal
            </span>
            <span className="text-[10px] uppercase tracking-[0.1em] text-zinc-500">
              born from this turn
            </span>
          </div>
          <p className="mt-0.5 text-[12px] leading-snug text-zinc-400">
            Pre-filled from what you just asked — review, adjust, one decision.
          </p>
        </div>
      </div>

      <div className="space-y-2.5 px-3.5 py-2.5">
        <label className="block">
          <span className="text-[11px] uppercase tracking-[0.08em] text-zinc-500">
            Name
          </span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="Goal name"
            className="field mt-1 w-full py-1.5 text-[12.5px]"
          />
        </label>

        <label className="block">
          <span className="text-[11px] uppercase tracking-[0.08em] text-zinc-500">
            Contract
          </span>
          <textarea
            value={contract}
            onChange={(e) => setContract(e.target.value)}
            aria-label="Goal contract"
            rows={5}
            className="field mt-1 w-full py-1.5 text-[12.5px] leading-snug"
          />
        </label>

        <div className="flex flex-wrap gap-2.5">
          <label className="block min-w-[180px] flex-1">
            <span className="text-[11px] uppercase tracking-[0.08em] text-zinc-500">
              Schedule (cron)
            </span>
            <input
              value={schedule}
              onChange={(e) => setSchedule(e.target.value)}
              aria-label="Goal schedule"
              placeholder="empty = manual — you run it; a cron fires on schedule"
              className="field mt-1 w-full py-1.5 text-[12.5px]"
            />
          </label>
          <label className="block w-32">
            <span className="text-[11px] uppercase tracking-[0.08em] text-zinc-500">
              Budget ($) — checked before every run
            </span>
            <input
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              aria-label="Goal budget in dollars"
              inputMode="decimal"
              className="field mt-1 w-full py-1.5 text-[12.5px]"
            />
          </label>
        </div>
        {!budgetOk && (
          <p className="text-[11.5px] text-rose-300">
            The budget must be a number above zero — it gates every run
            before it starts.
          </p>
        )}

        <p className="text-[11.5px] leading-snug text-zinc-500">
          Verifier: manual — you mark it satisfied; automatic checks can be
          added later. No tools are pre-granted: if a run needs one, the ask
          reaches your notifications like any other run.
        </p>
      </div>

      {error && (
        <div className="mx-3.5 mb-2.5 rounded-lg border border-rose-500/25 bg-rose-500/[0.06] px-2.5 py-1.5 text-[12px] text-rose-300">
          {error}
        </div>
      )}

      <div className="border-t border-white/[0.05] px-3.5 py-2.5">
        {/* The guarantees live AT the button — the sentence read at the
            moment of decision, pinned verbatim by the test. */}
        <p className="mb-2 text-[11.5px] leading-snug text-zinc-400">
          {GOAL_GUARANTEES}
        </p>
        <div className="flex flex-wrap items-center gap-1.5">
          {created ? (
            <>
              <span className="inline-flex items-center gap-1.5 text-[12px] text-emerald-300">
                <Check size={13} /> Goal created
              </span>
              <Link
                href="/autonomy"
                className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.02] px-2.5 py-1 text-[11.5px] text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft"
              >
                <DoorOpen size={11} className="shrink-0 text-accent-soft/70" />
                See your goal
              </Link>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => void create()}
                disabled={
                  creating || !budgetOk || !name.trim() || !contract.trim()
                }
                className="btn-accent px-3 py-1.5 text-[12px] disabled:opacity-50"
              >
                {creating ? (
                  <>
                    <Loader2 size={13} className="animate-spin" /> Creating…
                  </>
                ) : (
                  "Create goal"
                )}
              </button>
              {onDismiss && (
                <button
                  type="button"
                  onClick={onDismiss}
                  className="btn-ghost px-2.5 py-1.5 text-[12px]"
                >
                  Not now
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------ */
/* The chip + its per-turn dismissal                                         */
/* ------------------------------------------------------------------------ */

/** The whole goal-birth surface for ONE completed turn: renders nothing when
 *  the heuristic says no, the chip when it says yes, and the card once
 *  opened. Dismissal is PER TURN (component state, the workflow-chip idiom:
 *  it also self-retires when the conversation moves past this turn, since
 *  the page only mounts it on the newest settled reply). */
export function GoalBirth({
  userText,
  toolsUsed,
  projectId,
}: {
  userText: string;
  toolsUsed?: string[] | null;
  projectId?: string | null;
}) {
  const [dismissed, setDismissed] = useState(false);
  const [open, setOpen] = useState(false);
  if (dismissed || !shouldOfferGoal(userText, toolsUsed)) return null;
  if (open)
    return (
      <GoalContractCard
        userText={userText}
        projectId={projectId}
        onDismiss={() => setDismissed(true)}
      />
    );
  return (
    <div className="ml-11 mt-1.5 inline-flex items-center gap-1">
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-full border border-accent/25 bg-accent/[0.06] px-2.5 py-1 text-[11.5px] text-accent-soft transition-colors hover:bg-accent/[0.12]"
      >
        <Target size={12} />
        Keep doing this? → Make it a goal
      </button>
      <button
        type="button"
        aria-label="Not now"
        title="Not now"
        onClick={() => setDismissed(true)}
        className="grid h-6 w-6 place-items-center rounded-md text-zinc-600 transition-colors hover:bg-white/[0.06] hover:text-zinc-300"
      >
        <X size={11} />
      </button>
    </div>
  );
}
