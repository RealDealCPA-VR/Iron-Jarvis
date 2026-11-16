/**
 * v1.506.0 (north-star fix plan 8.4): one word for "something Iron Jarvis
 * offers and waits on". VOCABULARY.md row 45 says it is a **suggestion** (the
 * page that lists them all is Improvements); the Autonomy page still headed
 * its queue "Proposals" and its Approve press said "Approve and execute this
 * proposal", and the Watchers page sent people to "Autonomy → Proposals".
 *
 *  1. "proposal" is on the dashboard scan's retired list (RETIRED_ON_SCREEN,
 *     not RETIRED: the daemon's routes and ids still say it, and so does the
 *     Autonomy door's sentence in daemon/page_doors.py).
 *  2. The Autonomy page reads "Suggestions" and never "proposal".
 *  3. The pages that point at the Autonomy queue name it by its new heading.
 */
import { describe, expect, it } from "vitest";

import { RETIRED, RETIRED_ON_SCREEN, readableCopy } from "./helpers/retiredWords";

describe("proposal → suggestion (north-star 8.4)", () => {
  it("'proposal' is retired on screen, and only on screen", () => {
    const row = RETIRED_ON_SCREEN.find((r) => r.word === "proposal");
    expect(row).toBeDefined();
    expect(row!.re.test("Approve and execute this proposal")).toBe(true);
    expect(row!.re.test("Proposals")).toBe(true);
    expect(RETIRED.map((r) => r.word)).not.toContain("proposal");
  });

  it("the Autonomy page's queue is headed Suggestions and never says proposal", () => {
    const shown = readableCopy("app/autonomy/page.tsx").map((c) => c.text);
    expect(shown.filter((t) => /\bproposals?\b/i.test(t))).toEqual([]);
    expect(shown.some((t) => /^Suggestions/.test(t))).toBe(true);
    expect(shown).toContain("Approve and run this suggestion");
  });

  it("the Watchers page points at the queue by its new heading", () => {
    const shown = readableCopy("app/sentinels/page.tsx").map((c) => c.text);
    expect(shown.filter((t) => /\bproposals?\b/i.test(t))).toEqual([]);
    expect(shown.join(" ")).toContain("Autonomy → Suggestions");
  });
});
