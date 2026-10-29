/**
 * v1.406.0 part 4: the next-step box reads a reply's ending the DAEMON's
 * way. Every row of the fixture was written by the Python reading
 * (tests/test_closing_offer_twin_v1406.py, IJ_REGEN_CLOSING_OFFER=1) from
 * the v1.402.0 offer tables; the TS twin must agree on all of them.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { lastSentence, offerKind, offeredAction } from "@/lib/closingOffer";

interface Row {
  text: string;
  sentence: string;
  kind: "work" | "user" | "";
  action: string;
}

const rows: Row[] = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "closing-offer-v1406.json"), "utf8").replace(/\r\n/g, "\n"),
).rows;

describe("the closing-offer twin", () => {
  it("has every kind of row", () => {
    expect(rows.length).toBeGreaterThan(100);
    expect(new Set(rows.map((r) => r.kind))).toEqual(new Set(["work", "user", ""]));
  });

  it.each(rows.map((r) => [JSON.stringify(r.text).slice(0, 70), r] as const))("reads %s the daemon's way", (_n, r) => {
    expect(lastSentence(r.text)).toBe(r.sentence);
    expect(offerKind(r.text)).toBe(r.kind);
    expect(offeredAction(r.text)).toBe(r.action);
  });

  it("never throws on junk", () => {
    expect(offerKind(undefined as unknown as string)).toBe("");
    expect(offeredAction(null as unknown as string)).toBe("");
  });
});
