/**
 * v1.495.0 — the Autonomy card words the check-again nudge.
 *
 * A plain chat reply or an agent run's answer that Iron Jarvis's own check
 * found unfinished is sent back to fix it; the daemon publishes
 * `autonomy.nudged` with kind `checked_again` (core/nudges.NUDGE_KINDS) once
 * per note, and the card says it in words, never "1 other".
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

describe("the check-again nudge on the Autonomy card", () => {
  it("is said in words, singular and plural", () => {
    expect(systemWords(metrics({ checked_again: 1 }))).toBe(
      "Jarvis also moved work on by itself once (1 reply sent back because Jarvis's check found it unfinished).",
    );
    expect(systemWords(metrics({ checked_again: 2 }))).toBe(
      "Jarvis also moved work on by itself 2 times (2 replies sent back because Jarvis's check found them unfinished).",
    );
  });

  it("is never the words of an unknown kind", () => {
    expect(systemWords(metrics({ checked_again: 2 }))).not.toContain("other");
  });
});
