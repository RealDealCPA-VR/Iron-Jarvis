/**
 * Fourth review CS5 (the screen): a reply stopped by what was left of this
 * month's pay per use limit says THAT limit, never "this session's … cost
 * limit (list price)" — a limit the user never set, in the wrong unit. The
 * daemon marks such a met cap `"of": "pay_left"` (on the line's `capMet`).
 * The session's own cap words stay byte for byte.
 */
import { describe, expect, it } from "vitest";

import { capMetWords, decodeCapMet } from "@/lib/buildLongJobs";
import { codeNextStep, codeReplyFacts } from "@/lib/nextStep";

describe("a stop at what was left of the month's pay per use limit", () => {
  it("is decoded with whose limit it was, and only for a cost cap", () => {
    expect(decodeCapMet({ kind: "cost", limit: 0.4, of: "pay_left" })).toEqual({ kind: "cost", limit: 0.4, of: "pay_left" });
    expect(decodeCapMet({ kind: "cost", limit: 0.4, of: "weird" })).toEqual({ kind: "cost", limit: 0.4 });
    expect(decodeCapMet({ kind: "time", limit: 30, of: "pay_left" })).toEqual({ kind: "time", limit: 30 });
  });

  it("says the pay per use limit, never the session's list-price limit", () => {
    const words = capMetWords(decodeCapMet({ kind: "cost", limit: 0.4, of: "pay_left" })!);
    expect(words).toBe("This reply stopped at what was left of this month's $0.40 pay per use limit.");
    expect(words).not.toMatch(/session|list price/);
  });

  it("the session's own cap keeps its words byte for byte", () => {
    expect(capMetWords({ kind: "cost", limit: 5 })).toBe("This reply stopped at this session's $5.00 cost limit (list price).");
    expect(capMetWords({ kind: "time", limit: 120 })).toBe("This reply stopped at this session's 2-hour time limit.");
  });

  it("the next-step box names the same limit", () => {
    const pay = codeNextStep(codeReplyFacts({ content: "x", documents: ["a.py"], capMet: { kind: "cost", limit: 0.4, of: "pay_left" } }), null)!;
    expect(pay.recap).toBe("Changed 1 file. It stopped at what was left of this month's pay per use limit before it finished.");
    expect(pay.actions[0].testid).toBe("harness-cap-keep-going");
    const own = codeNextStep(codeReplyFacts({ content: "x", documents: ["a.py"], capMet: { kind: "cost", limit: 2 } }), null)!;
    expect(own.recap).toBe("Changed 1 file. It stopped at this session's limit before it finished.");
  });
});
