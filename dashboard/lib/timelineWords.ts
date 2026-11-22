/**
 * The Timeline's words, shared by its two views (v1.550.0): the graph
 * (components/harness/HarnessGraph) and the steps on one time axis
 * (components/harness/HarnessTimeline, which re-exports these as before).
 */

import type { StepCheck, TimelineStep } from "@/lib/buildTimeline";
import { kindWords, rowSubject, stepPhrase, stepPhraseText } from "@/lib/stepWords";
import type { TurnUsage } from "@/lib/types";
import { toolKind } from "@/lib/workTarget";

/** v1.390.0 review: a step Iron Jarvis ran ITSELF to check the reply (the
 *  finish check's test run, a project check) — never drawn as the model's. */
export const CHECK_WORDS: Readonly<Record<StepCheck, string>> = {
  finish: "Finish check",
  project: "Project check",
};

/** "~$0.31", "<$0.01", "~$0.31 list price" (a subscription's money is a
 *  list-price equivalent, never a charge); null when there is nothing honest
 *  to say (no price known, or a free model). `short` says "list". */
export function costWords(u: TurnUsage | undefined, short = false): string | null {
  const c = u?.cost_usd;
  if (typeof c !== "number" || !Number.isFinite(c) || c < 0) return null;
  const list = u?.list_price_equivalent === true;
  if (!list && c <= 0) return null;
  const amount = c > 0 && c < 0.005 ? "<$0.01" : `~$${c.toFixed(2)}`;
  return list ? `${amount} ${short ? "list" : "list price"}` : amount;
}

/** "120,400 in · 8,200 out" (+ " · 96,000 read from cache"). */
export function tokenWords(u: TurnUsage | undefined): string | null {
  if (!u || (u.input_tokens === undefined && u.output_tokens === undefined)) return null;
  // Nothing counted (the demo model, a model that reports none): nothing said.
  if (!u.input_tokens && !u.output_tokens) return null;
  const parts = [
    `${(u.input_tokens ?? 0).toLocaleString("en-US")} in`,
    `${(u.output_tokens ?? 0).toLocaleString("en-US")} out`,
  ];
  if (u.cache_read_input_tokens) parts.push(`${u.cache_read_input_tokens.toLocaleString("en-US")} read from cache`);
  return parts.join(" · ");
}

/** A step in the run log's words: "Read app.py", "Reading app.py"; a check
 *  Iron Jarvis ran itself says so first ("Finish check · Ran python"). */
export function stepLabel(step: Pick<TimelineStep, "name" | "target" | "state"> & { check?: StepCheck | null }): string {
  const own = ownStepLabel(step);
  return step.check ? `${CHECK_WORDS[step.check]} · ${own}` : own;
}

function ownStepLabel(step: Pick<TimelineStep, "name" | "target" | "state">): string {
  if (step.state === "working") {
    const kind = toolKind(step.name);
    const subject = rowSubject(kind, step.name, step.target);
    const lead = kindWords(kind, step.name, { subject: !!subject, running: true });
    return subject ? `${lead} ${subject.text}` : lead;
  }
  return stepPhraseText(stepPhrase(step.name, step.target));
}
