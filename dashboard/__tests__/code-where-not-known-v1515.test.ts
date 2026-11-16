/**
 * v1.515.0 (second review H12): the Session tab's "where this model runs"
 * line. The daemon no longer says "on this network" for a coding tool's OWN
 * local provider (Pi's or OpenCode's `ollama/...`), because that address is
 * set in the tool's own settings and Iron Jarvis cannot read it. It answers
 * `on_this_network: null` with a sentence of its own, and the tab must show
 * that sentence as sent. Before, the decoder dropped it and the tab drew
 * nothing.
 */
import { describe, expect, it } from "vitest";

import { decodeCodeOverview, whereLine } from "@/lib/codePanes";

const NOT_KNOWN = "Iron Jarvis cannot tell where Pi sends this model.";

function overview(where: unknown) {
  const o = decodeCodeOverview({ where });
  expect(o).not.toBeNull();
  return o!;
}

describe("where a coding tool's model runs", () => {
  it("shows the daemon's own sentence when it is not known", () => {
    const o = overview({ on_this_network: null, words: NOT_KNOWN });
    expect(o.where.onThisNetwork).toBeNull();
    expect(o.where.words).toBe(NOT_KNOWN);
    expect(whereLine(o)).toBe(NOT_KNOWN);
  });

  it("still says where it runs when the daemon knows", () => {
    expect(whereLine(overview({ on_this_network: true, words: "on this network" }))).toBe("Runs · on this network");
    expect(whereLine(overview({ on_this_network: false, words: "cloud" }))).toBe("Runs · cloud");
  });

  it("draws nothing when the daemon says nothing", () => {
    expect(whereLine(overview({ on_this_network: null, words: "" }))).toBe("");
    expect(whereLine(overview(undefined))).toBe("");
    expect(whereLine(null)).toBe("");
  });
});
