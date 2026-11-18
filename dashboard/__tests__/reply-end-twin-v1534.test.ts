/**
 * Fourth review CS1-CS3 (the dashboard twin): ONE reading of "was this coding
 * reply cut short?" — `lib/replyEnd.cutShort`, the twin of the daemon's
 * `harness/reply_end.cut_short`.
 *
 *  - the twin reads a reply line exactly as the daemon does: "length" for
 *    `truncated`, "cut_off" for a `route.cut_off` that was not recovered, ""
 *    otherwise (junk too);
 *  - its words are the daemon's own, key for key, in the same order (read
 *    from the Python file, so the two cannot drift);
 *  - a drift scan: the screen files that decide whether a coding reply
 *    finished read `truncated` / `cut_off` only through the twin.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { CUT_LENGTH, CUT_OFF, CUT_WORDS, cutShort } from "@/lib/replyEnd";

const root = join(__dirname, "..", "..");
const read = (rel: string) => readFileSync(join(root, rel), "utf8").replace(/\r\n/g, "\n");

describe("the twin reads a reply line as the daemon does", () => {
  it("length, cut off (not recovered), and nothing else", () => {
    expect(cutShort({ content: "Half", truncated: true })).toBe(CUT_LENGTH);
    expect(cutShort({ route: { cut_off: { times: 2, recovered: false } } })).toBe(CUT_OFF);
    expect(cutShort({ route: { cut_off: { times: 2 } } })).toBe(CUT_OFF);
    // A cut the route recovered from is a reply that finished.
    expect(cutShort({ route: { cut_off: { times: 1, recovered: true } } })).toBe("");
    // The length cut is read first (the daemon's order).
    expect(cutShort({ truncated: true, route: { cut_off: { recovered: true } } })).toBe(CUT_LENGTH);
    for (const junk of [null, undefined, "x", 3, [], { truncated: false }, { route: "x" }, { route: { cut_off: "x" } }])
      expect(cutShort(junk)).toBe("");
  });

  it("the words are the daemon's own, key for key, in the same order", () => {
    const py = read("src/iron_jarvis/harness/reply_end.py");
    const block = py.slice(py.indexOf("CUT_WORDS"), py.indexOf("}", py.indexOf("CUT_WORDS")));
    const pairs = [...block.matchAll(/^\s*(LENGTH|CUT_OFF):\s*"([^"]+)",?$/gm)].map((m) => [m[1], m[2]]);
    expect(pairs.length).toBe(2);
    const keyOf: Record<string, string> = {
      LENGTH: py.match(/^LENGTH = "([^"]+)"/m)![1],
      CUT_OFF: py.match(/^CUT_OFF = "([^"]+)"/m)![1],
    };
    expect(pairs.map(([k, w]) => [keyOf[k], w])).toEqual(Object.entries(CUT_WORDS));
    expect([CUT_LENGTH, CUT_OFF]).toEqual([keyOf.LENGTH, keyOf.CUT_OFF]);
  });
});

describe("the drift scan: the screen reads a reply's ending only through the twin", () => {
  // The files that decide whether a coding reply finished (its next-step box,
  // its one line's "cut off"). A new reader there fails here until it calls
  // cutShort.
  const READERS = ["dashboard/lib/nextStep.ts", "dashboard/components/harness/HarnessMessageRow.tsx"];
  it.each(READERS)("%s reads no truncated / cut_off of its own", (rel) => {
    const code = read(rel)
      .split("\n")
      .filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l))
      .join("\n");
    expect(code).toMatch(/cutShort\(/);
    expect(code.match(/\.truncated\b|\[["']truncated["']\]|\bcut_off\b/g)).toBeNull();
  });
});
