/**
 * v1.407.2 — the NEXT-STEP BOX reads "is this offer the user's call?" from
 * the daemon's reading ALONE (harness/closing_offer.user_owns, clause by
 * clause, through the generated twin lib/closingOffer). The box's own
 * `asksFirst` list (v1.406.0 part 4 review) moved into the daemon, so the
 * nudge and the box can never disagree.
 *
 * Every row of the ONE fixture the daemon wrote
 * (dashboard/__tests__/fixtures/closing-offer-v1406.json,
 * IJ_REGEN_CLOSING_OFFER=1) is driven through the box: a "user" row only
 * FILLS the message box, a "work" row keeps its one "Yes, do it" press, a
 * row with no offer draws no box.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { chatNextStep, chatReplyFacts, codeNextStep, codeReplyFacts } from "@/lib/nextStep";

interface Row {
  text: string;
  kind: "work" | "user" | "";
  action: string;
}

const rows: Row[] = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "closing-offer-v1406.json"), "utf8").replace(/\r\n/g, "\n"),
).rows;

const read = (p: string) => readFileSync(join(__dirname, "..", p), "utf8").replace(/\r\n/g, "\n");

const NEW_USER_ROWS = [
  "Want me to run the tests and then push?",
  "Want me to git push --force?",
  "Want me to rm -rf the build folder?",
  "Shall I transfer the funds?",
  "Shall I cancel the subscription?",
  "Want me to go ahead and run the deploy script?",
  "Shall I tag v2 and publish the release?",
  "Want me to run the migration on staging?",
  "Shall I sign the contract?",
  "Want me to revoke the token?",
];

describe("the next-step box follows the daemon's reading of a closing offer", () => {
  it("the fixture holds the v1.407.2 rows as the user's call", () => {
    for (const t of NEW_USER_ROWS) {
      const r = rows.find((x) => x.text === t);
      expect(r, t).toBeTruthy();
      expect(r!.kind, t).toBe("user");
    }
  });

  it.each(rows.map((r) => [JSON.stringify(r.text).slice(0, 70), r] as const))("box for %s", (_n, r) => {
    const chat = chatNextStep(chatReplyFacts({ content: r.text }));
    if (r.kind === "") {
      expect(chat).toBeNull();
      return;
    }
    if (r.kind === "work") {
      expect(chat!.actions.map((a) => [a.kind, a.how])).toEqual([["do_offer", "send"]]);
      return;
    }
    // The user's call: named, only fills the box — never a send.
    expect(chat!.actions.map((a) => a.how)).toEqual(["fill"]);
    expect(chat!.recap).toMatch(/your call/);
    const code = codeNextStep(
      codeReplyFacts({ content: r.text }),
      null,
    );
    expect(code?.actions.some((a) => a.how === "send") ?? false).toBe(false);
  });

  it("the box keeps no list of its own", () => {
    const src = read("lib/nextStep.ts");
    expect(src).not.toMatch(/const ASKS_FIRST|function asksFirst|asksFirst\(/);
    expect(read("lib/closingOffer.ts")).toMatch(/clauseOwns/);
  });
});
