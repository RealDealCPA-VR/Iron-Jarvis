/**
 * Step narration (v1.202.0, Wave B3).
 *
 * What these tests are really guarding:
 *   - the decomposed lane's plan.* events render as real step-by-step
 *     narration ("Step 2 of 3: <goal>") instead of falling through to the
 *     generic "Working…" — the Codex-feel seam. Payload shapes are copied
 *     from agents/decompose.py's publish sites, not invented:
 *       plan.created        {run_id, steps: [goal strings]}
 *       plan.step_started   {run_id, index, goal}         (index 0-based)
 *       plan.step_completed {run_id, index, ok}           (+ attempted:false
 *                            only on the budget-spent branch)
 *   - the payload carries NO verification field, so the completed label must
 *     not claim one ("done"/"failed", never "verified").
 *   - hostile-string hygiene (the doors-label rule): a model-written goal
 *     with newlines or 500 chars must stay one short line.
 *   - a malformed plan payload degrades to the existing generic label — it
 *     must never blank the progress line and never throw.
 *   - existing cases (tool.executed & co.) are byte-identical in behavior.
 *   - ONE implementation: app/chat/page.tsx must IMPORT stepLabel from
 *     components/chat/stepLabel rather than keep its own copy — an App
 *     Router page cannot export helpers (the .next/types checkFields pass
 *     rejects extra page exports), and the draftFromFence lesson says a
 *     second copy leaves every test green while the real call site rots.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { stepLabel } from "@/components/chat/stepLabel";
import type { IJEvent } from "@/lib/types";

let seq = 0;

function ev(
  type: string,
  payload: Record<string, unknown>,
  session_id = "sess-1",
): IJEvent {
  seq += 1;
  return {
    id: `ev-${seq}`,
    type,
    session_id,
    ts: "2026-08-22T00:00:00Z",
    payload,
  };
}

describe("stepLabel — plan.created", () => {
  it("renders the planned step count from decompose.py's real payload", () => {
    const label = stepLabel(
      ev("plan.created", {
        run_id: "run-created",
        steps: ["read the ledger", "compute totals", "write the summary"],
      }),
    );
    expect(label).toBe("Planned 3 steps");
  });

  it("uses the singular for a one-step plan", () => {
    expect(
      stepLabel(ev("plan.created", { run_id: "run-one", steps: ["do it"] })),
    ).toBe("Planned 1 step");
  });
});

describe("stepLabel — plan.step_started", () => {
  it("says 'Step k of n: <goal>' once plan.created has been seen", () => {
    stepLabel(
      ev("plan.created", { run_id: "run-kn", steps: ["a", "b", "c"] }),
    );
    expect(
      stepLabel(
        ev("plan.step_started", {
          run_id: "run-kn",
          index: 1,
          goal: "compute totals",
        }),
      ),
    ).toBe("Step 2 of 3: compute totals");
  });

  it("degrades to 'Step k' when plan.created was never seen (mid-run join)", () => {
    // A browser that connected after the plan was announced must not invent
    // a total it never heard.
    expect(
      stepLabel(
        ev("plan.step_started", {
          run_id: "run-midjoin-never-announced",
          index: 0,
          goal: "read the ledger",
        }),
      ),
    ).toBe("Step 1: read the ledger");
  });

  it("clips a long goal to one short line ending in an ellipsis", () => {
    const goal = "x".repeat(200);
    const label = stepLabel(
      ev("plan.step_started", { run_id: "run-clip", index: 0, goal }),
    );
    expect(label).not.toBeNull();
    expect(label!.endsWith("…")).toBe(true);
    // "Step 1: " + 60 clipped chars + ellipsis — nowhere near 200.
    expect(label!.length).toBeLessThan(80);
  });

  it("flattens hostile newlines/tabs in the goal — the line stays one line", () => {
    const label = stepLabel(
      ev("plan.step_started", {
        run_id: "run-hostile",
        index: 0,
        goal: "line one\n\nline two\t\tend",
      }),
    );
    expect(label).toBe("Step 1: line one line two end");
    expect(label).not.toMatch(/[\n\t]/);
  });
});

describe("stepLabel — plan.step_completed", () => {
  it("says done / failed off the payload's ok, and claims NO verification", () => {
    stepLabel(
      ev("plan.created", { run_id: "run-done", steps: ["a", "b", "c"] }),
    );
    const done = stepLabel(
      ev("plan.step_completed", { run_id: "run-done", index: 0, ok: true }),
    );
    expect(done).toBe("Step 1 of 3 done");
    // The payload carries no verification fact, so the label must not
    // fabricate one.
    expect(done!.toLowerCase()).not.toContain("verif");
    expect(
      stepLabel(
        ev("plan.step_completed", { run_id: "run-done", index: 1, ok: false }),
      ),
    ).toBe("Step 2 of 3 failed");
  });

  it("never calls a budget-skipped step 'failed' (decompose's attempted:false)", () => {
    // Exact additive-key payload from decompose.py's budget-spent branch:
    // {"run_id": ..., "index": ..., "ok": False, "attempted": False}
    stepLabel(
      ev("plan.created", { run_id: "run-budget", steps: ["a", "b"] }),
    );
    const label = stepLabel(
      ev("plan.step_completed", {
        run_id: "run-budget",
        index: 1,
        ok: false,
        attempted: false,
      }),
    );
    expect(label).toBe("Step 2 of 2 not attempted (step budget spent)");
    expect(label!.toLowerCase()).not.toContain("fail");
  });
});

describe("stepLabel — envelope.adapted", () => {
  // The adaptations are MACHINE tokens — the EXACT payload the runtime
  // publishes (pinned server-side in test_runtime_envelope_v1202.py as
  // ["tool_cap:3", "decomposed"]). The label must render them through
  // TurnReceipt's wordChange (the single renderer of this vocabulary), not
  // leak raw tokens into the user's progress line.
  it("words the runtime's real tokens: tool_cap:N + decomposed", () => {
    expect(
      stepLabel(
        ev("envelope.adapted", {
          provider: "ollama",
          model: "m1",
          adaptations: ["tool_cap:3", "decomposed"],
          source: "probed",
        }),
      ),
    ).toBe("Adapted to m1: 3 tools max, running step-by-step");
  });

  it("words a tool_cap-only adaptation", () => {
    expect(
      stepLabel(
        ev("envelope.adapted", {
          provider: "ollama",
          model: "qwen2.5:14b",
          adaptations: ["tool_cap:4"],
          source: "probed",
        }),
      ),
    ).toBe("Adapted to qwen2.5:14b: 4 tools max");
  });

  it("passes an unknown token through verbatim (new kinds read oddly, never vanish)", () => {
    expect(
      stepLabel(
        ev("envelope.adapted", {
          provider: "ollama",
          model: "m1",
          adaptations: ["strict_json", "tool_cap:2"],
          source: "probed",
        }),
      ),
    ).toBe("Adapted to m1: strict_json, 2 tools max");
  });

  it("says just the model when the adaptations list is empty or junk", () => {
    expect(
      stepLabel(
        ev("envelope.adapted", {
          provider: "ollama",
          model: "qwen2.5:14b",
          adaptations: [],
          source: "probed",
        }),
      ),
    ).toBe("Adapted to qwen2.5:14b");
    expect(
      stepLabel(
        ev("envelope.adapted", {
          provider: "ollama",
          model: "qwen2.5:14b",
          adaptations: [42, null, "  "],
          source: "probed",
        }),
      ),
    ).toBe("Adapted to qwen2.5:14b");
  });

  it("stays silent (null) when there is no model to name", () => {
    expect(
      stepLabel(ev("envelope.adapted", { adaptations: ["something"] })),
    ).toBeNull();
  });
});

describe("stepLabel — malformed payloads fall back, never blank, never throw", () => {
  it("plan.created without a steps array reads as the generic label", () => {
    expect(
      stepLabel(ev("plan.created", { run_id: "r", steps: "not-a-list" })),
    ).toBe("Working…");
    expect(stepLabel(ev("plan.created", {}))).toBe("Working…");
  });

  it("step events with a missing or hostile index read as the generic label", () => {
    expect(stepLabel(ev("plan.step_started", { run_id: "r" }))).toBe(
      "Working…",
    );
    expect(
      stepLabel(ev("plan.step_started", { run_id: "r", index: "2" })),
    ).toBe("Working…");
    expect(
      stepLabel(ev("plan.step_completed", { run_id: "r", index: -1 })),
    ).toBe("Working…");
  });

  it("a null payload does not throw on any of the new cases", () => {
    for (const type of [
      "plan.created",
      "plan.step_started",
      "plan.step_completed",
      "envelope.adapted",
    ]) {
      const hostile = {
        ...ev(type, {}),
        payload: null as unknown as Record<string, unknown>,
      };
      expect(() => stepLabel(hostile)).not.toThrow();
    }
  });
});

describe("stepLabel — existing cases unchanged", () => {
  it("tool.executed still renders exactly as before", () => {
    expect(
      stepLabel(
        ev("tool.executed", { tool: "read_document", ok: true, mode: "auto" }),
      ),
    ).toBe("Using read_document…");
    expect(stepLabel(ev("tool.executed", {}))).toBe("Using a tool…");
  });

  it("agent.started / unknown types still behave as before", () => {
    expect(stepLabel(ev("agent.started", {}))).toBe("Thinking…");
    expect(stepLabel(ev("something.unknown", {}))).toBeNull();
  });
});

describe("call site — the page imports THE implementation", () => {
  // The draftFromFence lesson: a second copy of the sequence left every test
  // green while the real call site drifted. The page cannot export helpers
  // (Next's .next/types checkFields rejects extra page exports), so the one
  // implementation lives in components/chat/stepLabel and the page must
  // import it — and must not keep a shadow copy of its own.
  const pageSrc = readFileSync(
    join(__dirname, "..", "app", "chat", "page.tsx"),
    "utf8",
  );

  it("app/chat/page.tsx imports stepLabel from components/chat/stepLabel", () => {
    expect(pageSrc).toMatch(
      /import\s*\{[^}]*\bstepLabel\b[^}]*\}\s*from\s*"@\/components\/chat\/stepLabel"/,
    );
  });

  it("app/chat/page.tsx no longer defines its own stepLabel", () => {
    expect(pageSrc).not.toContain("function stepLabel(");
  });
});
