/**
 * v1.480.0 (capability search) — the Autonomy card words the look-first nudge.
 *
 * A chat reply about to end on words is asked once whether it should look up
 * what Iron Jarvis can do first. When it does, the daemon publishes
 * `autonomy.nudged` with kind `look_first` (core/nudges.NUDGE_KINDS), and the
 * card says it in words, never "1 other" (the words a kind with no row in
 * SYSTEM_WORDS gets).
 */

import { describe, expect, it } from "vitest";

import { decodeMetrics, systemWords } from "@/lib/autonomyMetrics";

function metrics(byKind: Record<string, number>) {
  const total = Object.values(byKind).reduce((a, b) => a + b, 0);
  return decodeMetrics({
    days: 30,
    tasks: 4,
    successful: 4,
    hands_off: 4,
    hands_off_pct: 100,
    interruptions: { total: 0, avoidable: 0, per_task: 0, avoidable_per_task: 0 },
    nudges: { human: 0, per_successful_task: 0, system: { total, by_kind: byKind } },
  })!;
}

describe("the look-first nudge on the Autonomy card", () => {
  it("is said in words, singular and plural", () => {
    expect(systemWords(metrics({ look_first: 1 }))).toBe(
      "Jarvis also moved work on by itself once (1 reply that looked up a feature before it ended).",
    );
    expect(systemWords(metrics({ look_first: 3 }))).toBe(
      "Jarvis also moved work on by itself 3 times (3 replies that looked up a feature before they ended).",
    );
  });

  it("is never the words of an unknown kind", () => {
    expect(systemWords(metrics({ look_first: 2 }))).not.toContain("other");
  });
});
