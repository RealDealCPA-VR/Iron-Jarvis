/**
 * stepLabel — one raw session event in, one short human progress line out
 * (or null to skip events that don't read well as a step).
 *
 * Moved verbatim out of app/chat/page.tsx in v1.202.0 so it can be
 * unit-tested: an App Router page may not carry extra named exports (the
 * generated .next/types check rejects them and tsc/build go red), so "just
 * export it" was never an option. ONE implementation lives here and the page
 * imports it — the draftFromFence lesson says two copies drift, so the test
 * file pins the page's import as well as the behavior.
 *
 * v1.202.0 (Wave B3): the decomposed agent lane's plan.* events reach the
 * chat stream but used to fall through to `null`, so a run genuinely working
 * step by step showed the same generic "Working…" as a stuck one. Payload
 * shapes read from agents/decompose.py, not guessed:
 *   plan.created        {run_id, steps: [goal, ...]}
 *   plan.step_started   {run_id, index, goal}            (index 0-based)
 *   plan.step_completed {run_id, index, ok}              (+ attempted: false
 *                        ONLY on the budget-spent branch; there is NO
 *                        verification field in the payload, so no verified
 *                        wording is invented here)
 *   envelope.adapted    {provider, model, adaptations: [...], source}
 * A malformed plan payload falls back to the existing generic "Working…" —
 * a hostile or half-built event must never blank the progress line or throw.
 */

import type { IJEvent } from "@/lib/types";
import { wordChange } from "@/components/chat/TurnReceipt";

// A few agent states worth naming; anything else falls back to "Working…".
const STATE_LABEL: Record<string, string> = {
  initializing: "Getting ready…",
  running: "Working…",
  waiting: "Waiting…",
  paused: "Paused…",
  delegating: "Bringing in a helper…",
  reviewing: "Reviewing the work…",
  completed: "Wrapping up…",
};

// plan.step_started carries only {index, goal} — the TOTAL lives in a
// different event (plan.created's steps list), so the size is remembered per
// run to say "step 2 of 4". A watcher who joined mid-run and never saw
// plan.created degrades honestly to "Step 2" rather than inventing a total.
// Bounded so a long-lived tab cannot grow it forever (Map preserves insertion
// order — the oldest run is the one evicted).
const planSizes = new Map<string, number>();
const PLAN_SIZES_MAX = 32;

function rememberPlanSize(runId: unknown, n: number): void {
  if (typeof runId !== "string" || !runId) return;
  if (!planSizes.has(runId) && planSizes.size >= PLAN_SIZES_MAX) {
    const oldest = planSizes.keys().next().value;
    if (oldest !== undefined) planSizes.delete(oldest);
  }
  planSizes.set(runId, n);
}

// Hostile-string hygiene (the doors-label rule): a goal is model-written and
// may carry newlines or run arbitrarily long; the progress line is ONE line.
function clip(s: string, max = 60): string {
  const flat = s.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

/** 0-based step index, or null when the payload doesn't carry a sane one. */
function stepIndex(p: Record<string, unknown>): number | null {
  const i = p.index;
  return typeof i === "number" && Number.isInteger(i) && i >= 0 ? i : null;
}

/** "Step 2 of 4" when the plan size is known, "Step 2" when it is not. */
function stepPhrase(p: Record<string, unknown>): string | null {
  const i = stepIndex(p);
  if (i === null) return null;
  const total =
    typeof p.run_id === "string" ? planSizes.get(p.run_id) : undefined;
  return total !== undefined && i < total
    ? `Step ${i + 1} of ${total}`
    : `Step ${i + 1}`;
}

// Turn one raw session event into a short, human-friendly progress line (or null
// to skip events that don't read well as a step).
export function stepLabel(e: IJEvent): string | null {
  const p = e.payload || {};
  switch (e.type) {
    case "agent.started":
      return "Thinking…";
    case "agent.state_changed": {
      // Backend payload is {from, to}; tolerate a `state` alias just in case.
      const to = (p.to ?? p.state) as string | undefined;
      if (!to) return "Working…";
      return STATE_LABEL[to.toLowerCase()] ?? "Working…";
    }
    case "tool.executed": {
      const tool = p.tool as string | undefined;
      return tool ? `Using ${tool}…` : "Using a tool…";
    }
    case "tool.denied": {
      const tool = p.tool as string | undefined;
      return tool ? `Skipped ${tool} (not permitted)` : "Skipped a tool";
    }
    case "provider.failed": {
      const provider = p.provider as string | undefined;
      return `Provider ${provider} failed — ${String(p.error || "").slice(0, 120)}`;
    }
    case "provider.downgraded":
      return "Model not connected — using offline mock (connect a model)";
    case "agent.completed":
      return "Finishing up…";
    case "plan.created": {
      // decompose.py publishes {run_id, steps: [goal strings]}.
      const steps = p.steps;
      if (!Array.isArray(steps) || steps.length === 0) return "Working…";
      rememberPlanSize(p.run_id, steps.length);
      return `Planned ${steps.length} step${steps.length === 1 ? "" : "s"}`;
    }
    case "plan.step_started": {
      const phrase = stepPhrase(p);
      if (!phrase) return "Working…";
      const goal = typeof p.goal === "string" ? clip(p.goal) : "";
      return goal ? `${phrase}: ${goal}` : phrase;
    }
    case "plan.step_completed": {
      const phrase = stepPhrase(p);
      if (!phrase) return "Working…";
      // `attempted: false` is decompose's additive key for the budget-spent
      // branch — that step never ran, and "failed" would be a lie about it.
      if (p.attempted === false) {
        return `${phrase} not attempted (step budget spent)`;
      }
      // The payload carries ok, nothing more — no verification fact exists
      // here, so none is claimed.
      return p.ok === false ? `${phrase} failed` : `${phrase} done`;
    }
    case "envelope.adapted": {
      // Quiet phrasing: the loop bent itself to fit the model; say so in one
      // breath, never as an alarm. Payload {provider, model, adaptations,
      // source} — no model name means nothing worth a line. The runtime's
      // adaptations are MACHINE tokens ("tool_cap:3", "decomposed"), and
      // TurnReceipt's wordChange is THE single renderer of that vocabulary
      // (one-renderer rule) — never inline a second translation here. An
      // unknown token passes through verbatim by wordChange's own contract.
      const model =
        typeof p.model === "string" && p.model ? clip(p.model, 40) : "";
      if (!model) return null;
      const adaptations = Array.isArray(p.adaptations)
        ? p.adaptations.filter(
            (a): a is string => typeof a === "string" && a.trim() !== "",
          )
        : [];
      if (adaptations.length === 0) return `Adapted to ${model}`;
      const worded = adaptations.map(wordChange);
      return `Adapted to ${model}: ${clip(worded.join(", "), 100)}`;
    }
    default:
      return null;
  }
}
