/**
 * v1.421.0 (Wave 1, A5) — the Autonomy card words the new system nudge.
 *
 * A model that writes a step out as text (GLM's `<tool_call>…`) is told once
 * to run it for real — in a coding session's turn (both chat lanes) and now in
 * an agent run too. Each publishes `autonomy.nudged` with kind
 * `text_tool_call` (core/nudges.NUDGE_KINDS), and the card says it in words —
 * never "1 other" (the words a kind with no row in SYSTEM_WORDS gets).
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

describe("the text-tool-call nudge on the Autonomy card", () => {
  it("is said in words, singular and plural", () => {
    expect(systemWords(metrics({ text_tool_call: 1 }))).toBe(
      "Jarvis also moved work on by itself once (1 step a model wrote out as text and was told to run).",
    );
    expect(systemWords(metrics({ text_tool_call: 2, plan_run: 1 }))).toBe(
      "Jarvis also moved work on by itself 3 times (2 steps a model wrote out as text and was told to run, " +
        "1 approved plan ran by itself).",
    );
  });

  it("is never the words of an unknown kind", () => {
    expect(systemWords(metrics({ text_tool_call: 1 }))).not.toContain("other");
  });
});
