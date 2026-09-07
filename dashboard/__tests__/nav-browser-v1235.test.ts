/**
 * The Browser page's NAV ENTRY, pinned (v1.235.0, Ship 1 of the Browser capability).
 *
 * Decision D03 is "one user-facing browser surface": the existing /computeruse page
 * BECOMES Browser rather than a second top-level page appearing beside it. That makes
 * the nav entry the whole of the rename, and it is the one place a rename can go
 * quietly wrong in three different ways.
 *
 * What each assertion catches, and the silent failure it would otherwise be:
 *
 * - The LABEL is Browser. Renaming the page header without renaming the rail leaves
 *   the user looking for "Browser" in a list that says "Computer Control", which is
 *   exactly the hesitation VOCABULARY.md exists to prevent. Nothing errors.
 * - The ROUTE is still /computeruse. Identities are contracts (VOCABULARY.md's
 *   three-layer rule): docs, the Help tile and nav.test.ts's frozen RAIL all name that
 *   path, and a "tidy" rename to /browser would break every one of them at once while
 *   the page itself still rendered fine in isolation.
 * - The old label survives as a SEARCH ALIAS. A user who learned "Computer Control"
 *   must still find the page. An alias list that drops the old word makes the feature
 *   unreachable by its own former name.
 * - "browser" is NOT an alias. nav.test.ts refuses an alias that merely restates the
 *   label, so leaving the old `"browser"` entry in place turns a passing suite red in
 *   a file this change does not own — the failure would read as unrelated.
 * - The entry stays in the Automate SECTION. nav.test.ts's RAIL pins section
 *   membership by href; moving the entry passes this file and fails that one.
 * - The blurb says what it DOES. The rail's blurb is the only sentence a first-time
 *   user reads before clicking, and the old one described the headless Chromium, not
 *   the browser they are looking at.
 */

import { describe, expect, it } from "vitest";

import { NAV } from "@/lib/nav";

const ROUTE = "/computeruse";

function browserEntry() {
  for (const section of NAV) {
    for (const item of section.items) {
      if (item.href === ROUTE) return { item, section };
    }
  }
  throw new Error(`no nav entry for ${ROUTE}`);
}

describe("the Browser nav entry", () => {
  it("is labelled Browser", () => {
    expect(browserEntry().item.label).toBe("Browser");
  });

  it("keeps the /computeruse route", () => {
    // Identities are contracts: docs, the Help tile and nav.test.ts's frozen RAIL
    // all name this path. D03 renames the surface, never the route.
    expect(browserEntry().item.href).toBe(ROUTE);
  });

  it("keeps the old label as a search alias", () => {
    const aliases = (browserEntry().item.aliases ?? []).map((a) => a.toLowerCase());
    expect(aliases).toContain("computer control");
  });

  it("does not alias the word it is now called", () => {
    // nav.test.ts refuses an alias that restates the label. "browser" was an alias
    // BEFORE the rename and has to go, or that file goes red.
    const aliases = (browserEntry().item.aliases ?? []).map((a) => a.toLowerCase());
    expect(aliases).not.toContain("browser");
  });

  it("offers the words a user would actually search for", () => {
    const aliases = (browserEntry().item.aliases ?? []).map((a) => a.toLowerCase());
    for (const word of ["chrome", "tabs"]) {
      expect(aliases).toContain(word);
    }
  });

  it("stays in the Automate section", () => {
    expect(browserEntry().section.label).toBe("Automate");
  });

  it("describes the user's own browser, not the headless one", () => {
    const blurb = (browserEntry().item.blurb ?? "").toLowerCase();
    expect(blurb).toContain("your own browser");
    // The old blurb promised "agents drive a real browser", which describes the
    // separate Playwright Chromium sitting further down the same page.
    expect(blurb).not.toContain("agents drive a real browser");
  });
});
