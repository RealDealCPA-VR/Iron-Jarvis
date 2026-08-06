import { describe, expect, it } from "vitest";
import {
  mentionTokens,
  mentionText,
  predictSpeakers,
} from "../components/agents/RoundTable";
import type { Participant } from "../components/agents/identity";

/**
 * The round-table's speaking-indicator PREDICTS who a round will call on —
 * cosmetically, since the /say response is the truth — but a wrong "only X
 * speaking" chip while everyone answers reads as a bug. These pins keep the
 * client prediction EQUAL to the daemon's rule (agents/threads.py
 * _MENTION_RE + _mentioned): if the backend rule changes, change both and
 * these fixtures in the same PR.
 */

const p = (source: string, name: string, role = "participant"): Participant => ({
  key: `${source}:${name}`,
  source: source as Participant["source"],
  name,
  role,
});

const PANEL = [
  p("builtin", "planner", "lead"),
  p("dynamic", "builder", "builder"),
  p("remote", "hermes-mac-mini", "critic"),
  p("dynamic", "Tax Expert", "researcher"),
];

const keys = (message: string) => predictSpeakers(message, PANEL).map((x) => x.key);

describe("mention prediction matches the daemon's rule", () => {
  it("no mention → [] (everyone speaks)", () => {
    expect(keys("what do you all think?")).toEqual([]);
  });

  it("plain @name directs the round", () => {
    expect(keys("@planner what first?")).toEqual(["builtin:planner"]);
  });

  it("trailing sentence punctuation is forgiven: '@builder.' targets builder", () => {
    expect(keys("nice work @builder.")).toEqual(["dynamic:builder"]);
    expect(keys("over to you, @builder-")).toEqual(["dynamic:builder"]);
  });

  it("a mid-word @ is an address, never a mention — an email must NOT shrink the prediction", () => {
    // The daemon's lookbehind rejects planner@builder.io; predicting
    // "only builder" here while everyone answers was the exact drift bug.
    expect(keys("send it to planner@builder.io please")).toEqual([]);
    expect(mentionTokens("planner@builder.io")).toEqual([]);
  });

  it("'@builder.x' is one token that matches nobody — not a builder mention", () => {
    expect(keys("check @builder.x for me")).toEqual([]);
  });

  it("dotted/hyphenated names work bare", () => {
    expect(keys("@hermes-mac-mini status?")).toEqual(["remote:hermes-mac-mini"]);
  });

  it("roles and case-insensitivity match, in panel order (never mention order)", () => {
    expect(keys("@CRITIC then @Lead")).toEqual([
      "builtin:planner",
      "remote:hermes-mac-mini",
    ]);
  });

  it('quoted mentions reach names with spaces: @"Tax Expert"', () => {
    expect(keys('@"Tax Expert" is this deductible?')).toEqual(["dynamic:Tax Expert"]);
  });

  it("tokens matching nobody are ignored → everyone speaks", () => {
    expect(keys("@nobody-here thoughts?")).toEqual([]);
  });
});

describe("the composer writes mentions the daemon can read back", () => {
  it("bare when the name survives the bare-token grammar", () => {
    expect(mentionText("builder")).toBe("@builder");
    expect(mentionText("hermes-mac-mini")).toBe("@hermes-mac-mini");
  });
  it("quoted when it would not: spaces, or trailing chars trimming would eat", () => {
    expect(mentionText("Tax Expert")).toBe('@"Tax Expert"');
    expect(mentionText("helper-")).toBe('@"helper-"');
  });
  it("round-trips: inserted mention predicts exactly that participant", () => {
    for (const part of PANEL) {
      expect(keys(`${mentionText(part.name)} take this`)).toEqual([part.key]);
    }
  });
});
