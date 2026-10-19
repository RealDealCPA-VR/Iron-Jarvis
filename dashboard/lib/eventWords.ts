/**
 * PLAIN WORDS FOR A LEDGER EVENT (v1.232.0 audit U12, v1.314.0 UX wave 2;
 * lifted into lib/ in v1.354.0, product-readiness G20).
 *
 * The Time-travel feed (components/TimeTravelFeed) said these words first.
 * The run page's live activity rows printed `JSON.stringify(payload)` for
 * any event it had no special case for, and it cannot import them from the
 * feed: ~11 test files mock `@/components/TimeTravelFeed` with a factory that
 * exports the component alone, and a mocked module throws on any other
 * export. So the table lives here, dependency-light, and the feed re-exports
 * it (one table, never two — `dashboard/__tests__/run-page-words-v13540.test.ts`
 * pins the feed's copy equal to this one until it does).
 */

import { providerDisplay } from "@/lib/onboarding";

/** Plain words for the event types the ledger projects straight into a row's
 *  `summary` (`_event_summary` says "provider.routed <model>" for a decision,
 *  and `actor` falls back to the type when nothing stamped an origin). A row
 *  read "decision by provider.routed / provider.routed" — the type twice and
 *  no words. Unknown types keep their summary verbatim (a new kind must read
 *  oddly, never vanish). */
export const EVENT_WORDS: Record<string, string> = {
  "provider.routed": "Picked a model for this turn",
  "provider.failover": "Switched provider after a failure",
  // v1.314.0: never "mock" in words, and true to the router since v1.162.0 —
  // this event now means the chosen model could not answer (it refuses; the
  // only time the demo model still answers is the mock-default trap, where
  // no real model answered either).
  "provider.downgraded": "No real model answered",
  "autonomy.proposed": "Proposed an action on its own",
  "autonomy.executed": "Ran an action on its own",
  "llm.completed": "Model call finished",
  "session.created": "Session started",
  "session.completed": "Session finished",
  "agent.started": "Agent started",
  "agent.state_changed": "Agent changed state",
  "agent.completed": "Agent finished",
  "action.reverted": "Action undone",
  "tool.executed": "Ran a tool",
  "tool.denied": "Refused a tool",
  // v1.314.0 (UX wave 2): the events the Activity page printed raw.
  "delegation.started": "Handed work to a teammate",
  "delegation.completed": "Teammate finished",
  "comm.desktop": "Sent a desktop notification",
};

/** v1.314.0: "agent running→completed" (eval/observability._event_summary
 *  for agent.state_changed) as a sentence. */
const AGENT_STATE_RE = /^agent ([a-z_]+)\s*→\s*([a-z_]+)$/;
/** v1.314.0: a token row ("<provider>/<model> · <in>+<out> tok", the same
 *  _event_summary). Anchored on the " tok" tail so a path with a slash in an
 *  ordinary summary is never mistaken for one. */
const TOKEN_SUMMARY_RE = /^([\w.-]+)\/(\S*) · (\d+\+\d+ tok)$/;

const EVENT_TYPE_RE = /^([a-z_]+\.[a-z_.]+)\b\s*(.*)$/s;

/** The row's second line: the event's human label (plus the summary's own
 *  detail, e.g. the model picked) when the summary is a bare event type. */
export function humanSummary(summary: string | null | undefined): string {
  const s = (summary ?? "").trim();
  if (!s) return "";
  const st = AGENT_STATE_RE.exec(s);
  if (st) return `Agent went from ${st[1]} to ${st[2]}`;
  // A token row names the provider in words; the scripted model is "Demo
  // model", never "mock" (its model id is "mock-1", so it is dropped too).
  // A real row keeps its model id — it is the record of what ran.
  const tok = TOKEN_SUMMARY_RE.exec(s);
  if (tok) {
    if (tok[1] === "mock") return `Demo model · ${tok[3]}`;
    return [providerDisplay(tok[1]), tok[2], tok[3]].filter(Boolean).join(" · ");
  }
  const m = EVENT_TYPE_RE.exec(s);
  if (!m) return s;
  const words = EVENT_WORDS[m[1]];
  if (!words) return s;
  const detail = m[2].trim();
  return detail ? `${words} · ${detail}` : words;
}
