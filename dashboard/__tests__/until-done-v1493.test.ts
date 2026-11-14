/**
 * v1.493.0 — the check-again loop on the screen. A coding reply's finish
 * record may carry `again` (how often Iron Jarvis sent the model back to work
 * after its check, how the loop ended, how many points were still open). The
 * work line says it, warns when the loop ended with the work not finished,
 * and the next-step box only offers "Fix the failing tests" when the tests
 * are what is failing. A record without `again` reads exactly as before.
 */
import { describe, expect, it } from "vitest";

import { decodeFinish } from "@/lib/buildSessions";
import { codeNextStep, type GitFacts, type ReplyFacts } from "@/lib/nextStep";
import {
  againWords,
  finishNotDone,
  finishWarn,
  finishWords,
  testsFailing,
  type FinishRecord,
} from "@/lib/stepWords";

const BASE = { tests: "failed", command: "pytest -q", removed: 0, kept: 0, points: 2, unmet: 0, after: "" };

function rec(extra: Record<string, unknown>): FinishRecord {
  const out = decodeFinish({ ...BASE, ...extra });
  if (!out) throw new Error("the record did not decode");
  return out;
}

describe("the finish record's `again`", () => {
  it("is decoded only in its known shape", () => {
    expect(rec({ again: { rounds: 2, ended: "stuck", open: 1 } }).again).toEqual({ rounds: 2, ended: "stuck", open: 1 });
    expect(rec({ again: { rounds: 0, ended: "", open: 0 } }).again).toEqual({ rounds: 0, ended: "", open: 0 });
    expect(rec({ again: { rounds: 1, ended: "done" } }).again).toEqual({ rounds: 1, ended: "done", open: 0 });
    for (const bad of [
      { rounds: 1, ended: "whatever", open: 0 },
      { rounds: 99, ended: "done", open: 0 },
      { rounds: -1, ended: "done", open: 0 },
      { rounds: "1", ended: "done", open: 0 },
      "stuck",
      null,
    ]) {
      expect(rec({ again: bad }).again).toBeUndefined();
    }
    // An older record has none, and reads exactly as it did.
    expect(rec({}).again).toBeUndefined();
    expect(finishWords(rec({}))).toBe("Checked: tests failed");
  });

  it("says what the loop did in plain words", () => {
    expect(againWords({ rounds: 0, ended: "done", open: 0 })).toBe("");
    expect(againWords({ rounds: 1, ended: "done", open: 0 })).toBe("sent back to work once");
    expect(againWords({ rounds: 3, ended: "limit", open: 0 })).toBe("sent back to work 3 times, stopped at its limit");
    expect(againWords({ rounds: 1, ended: "stuck", open: 0 })).toBe(
      "sent back to work once, stopped: two tries changed nothing",
    );
    expect(againWords({ rounds: 0, ended: "steps", open: 0 })).toBe("ran out of steps");
    expect(againWords({ rounds: 0, ended: "unchecked", open: 0 })).toBe("tests not run again after the last change");
    expect(againWords({ rounds: 1, ended: "blocked", open: 2 })).toBe("sent back to work once, 2 points not done");
  });

  it("rides the work line after the tests' words", () => {
    expect(finishWords(rec({ after: "passed", again: { rounds: 1, ended: "done", open: 0 } }))).toBe(
      "Checked: tests failed, then fixed, sent back to work once",
    );
    expect(finishWords(rec({ again: { rounds: 1, ended: "stuck", open: 0 } }))).toBe(
      "Checked: tests failed, sent back to work once, stopped: two tries changed nothing",
    );
  });

  it("warns when the loop ended with the work not finished, never when it ended done", () => {
    const fixed = rec({ after: "passed", again: { rounds: 2, ended: "done", open: 0 } });
    expect(finishNotDone(fixed)).toBe(false);
    expect(finishWarn(fixed)).toBe(false);
    for (const ended of ["stuck", "limit", "steps", "unchecked", "blocked"]) {
      const f = rec({ tests: "passed", again: { rounds: 1, ended, open: ended === "blocked" ? 1 : 0 } });
      expect(finishNotDone(f)).toBe(true);
      expect(finishWarn(f)).toBe(true);
      // The tests passed: only the loop's ending makes this a warning.
      expect(testsFailing(f)).toBe(false);
    }
    // "outside": the tests fail and the reply says the user asked to leave
    // them. The loop did not end unfinished; the warn mark stays only
    // because the tests really fail.
    const outside = rec({ again: { rounds: 0, ended: "outside", open: 0 } });
    expect(outside.again).toEqual({ rounds: 0, ended: "outside", open: 0 });
    expect(finishNotDone(outside)).toBe(false);
    expect(testsFailing(outside)).toBe(true);
    expect(finishWarn(outside)).toBe(true);
    expect(finishWords(outside)).toBe(
      "Checked: tests failed, the reply says the failures are outside the request",
    );
    // No `again`: the old rule, unchanged.
    expect(finishWarn(rec({}))).toBe(true);
    expect(finishWarn(rec({ after: "passed" }))).toBe(false);
    expect(finishNotDone(rec({}))).toBe(false);
  });
});

describe("the next-step box", () => {
  const git: GitFacts | null = null;
  const reply = (finish: FinishRecord): ReplyFacts => ({
    content: "Done.",
    documents: 1,
    finish,
    stopped: null,
    cutOff: false,
    failed: false,
  });

  it("offers the tests fix only when the tests are what is failing", () => {
    const blocked = codeNextStep(reply(rec({ tests: "passed", unmet: 1, again: { rounds: 1, ended: "blocked", open: 1 } })), git);
    expect(blocked?.actions.map((a) => a.kind)).not.toContain("fix_tests");
    expect(blocked?.recap).toContain("1 point of the request is not done.");
    const failing = codeNextStep(reply(rec({ again: { rounds: 1, ended: "stuck", open: 0 } })), git);
    expect(failing?.actions.map((a) => a.kind)).toContain("fix_tests");
  });

  it("no longer says a point needs work once the loop saw it settled", () => {
    const settled = codeNextStep(
      reply(rec({ tests: "passed", unmet: 1, again: { rounds: 1, ended: "done", open: 0 } })), git,
    );
    expect(settled?.recap ?? "").not.toContain("need");
    expect(settled?.recap ?? "").not.toContain("not done");
    // An older record (no `again`) keeps its old sentence.
    const old = codeNextStep(reply(rec({ tests: "passed", unmet: 1 })), git);
    expect(old?.recap).toContain("1 point of the request still needs work.");
  });
});
