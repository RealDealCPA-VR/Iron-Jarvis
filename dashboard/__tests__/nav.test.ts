import { describe, expect, it } from "vitest";

import { NAV, NAV_ENTRIES, type NavEntry } from "../lib/nav";

/**
 * The nav catalogue is the data behind the global search ("one front door").
 * These tests exist so a page can never ship UNFINDABLE: if you add a route to
 * NAV without the words a real person would type, this file goes red.
 */

/** How the search box will read an entry: its label plus its plain-English aliases. */
function matches(entry: NavEntry, query: string): boolean {
  const q = query.toLowerCase();
  return (
    entry.label.toLowerCase().includes(q) ||
    entry.aliases.some((a) => a.toLowerCase().includes(q))
  );
}

function hrefsFor(query: string): string[] {
  return NAV_ENTRIES.filter((e) => matches(e, query)).map((e) => e.href);
}

describe("nav catalogue — every entry is searchable at all", () => {
  it("has entries", () => {
    expect(NAV_ENTRIES.length).toBeGreaterThan(0);
  });

  it.each(NAV_ENTRIES.map((e) => [e.href, e] as const))(
    "%s carries at least two aliases",
    (_href, entry) => {
      // One alias is a synonym; two is the start of actually covering how
      // different people phrase the same want.
      expect(entry.aliases.length).toBeGreaterThanOrEqual(2);
      expect(entry.aliases.every((a) => a.trim().length > 0)).toBe(true);
    },
  );

  it.each(NAV_ENTRIES.map((e) => [e.href, e] as const))(
    "%s carries a blurb for the result row",
    (_href, entry) => {
      // A result with no blurb makes the user click to find out what it is.
      expect(entry.blurb.trim().length).toBeGreaterThan(0);
      // ONE line: a result row is one line tall. A paragraph gets clipped, so
      // the part that would have explained the page is the part that is lost.
      expect(entry.blurb).not.toContain("\n");
      expect(entry.blurb.length).toBeLessThanOrEqual(120);
    },
  );

  it.each(NAV_ENTRIES.map((e) => [e.href, e] as const))(
    "%s has a label and an icon",
    (_href, entry) => {
      expect(entry.label.trim().length).toBeGreaterThan(0);
      expect(entry.icon).toBeTruthy();
    },
  );

  it("has no lazy aliases that just restate the label", () => {
    // "Usage" aliased to "usage" helps nobody — the label already matches.
    for (const entry of NAV_ENTRIES) {
      const label = entry.label.toLowerCase();
      const distinct = entry.aliases.filter((a) => a.toLowerCase() !== label);
      expect(distinct.length).toBeGreaterThanOrEqual(2);
    }
  });
});

/**
 * REPORTED, this month, by real users who could not find a page they were
 * looking straight at. Each row is the phrase someone actually typed or asked
 * for, mapped to the page that answers it. These are the regression pins — if
 * a rename or a reshuffle breaks one, the user is lost again.
 */
describe("nav catalogue — the friction phrases resolve", () => {
  it.each([
    // "I just want to rename my endpoint" — went hunting in Settings for days.
    ["rename endpoint", "/connections"],
    ["endpoints", "/connections"],
    ["ollama", "/connections"],
    ["vllm", "/connections"],
    ["api keys", "/connections"],
    // "how do I redact a client's info out of this PDF"
    ["redact", "/documents"],
    ["pii", "/documents"],
    // Nobody calls it "Memory" on the first try.
    ["memory base", "/memory"],
    ["notes", "/memory"],
    ["brain", "/memory"],
    // Per-tool approval lives under Tools, not Settings.
    ["auto-approve", "/tools"],
    ["mcp", "/tools"],
    ["packs", "/tools"],
    // Billing anxiety, phrased two ways.
    ["tokens", "/usage"],
    ["cost", "/usage"],
    // "where are my local models"
    ["local models", "/fleet"],
    // A dead ollama endpoint is BOTH a connection to edit and a fleet member
    // that stopped serving — the search must offer both pages, not pick one.
    ["ollama", "/fleet"],
    ["vllm", "/fleet"],
    // "what version am I on" / "is there an update"
    ["version", "/updates"],
    ["update", "/updates"],
    // v1.144.0 — the feedback that started this wave was phrased as symptoms,
    // never as "profile": people ask for shorter answers, for English, or for
    // a dyslexia-friendly shape. All three must land on /you.
    ["shorter answers", "/you"],
    ["answer in english", "/you"],
    ["dyslexia", "/you"],
    ["about me", "/you"],
    // The context spine has to be findable by the words people use for the
    // thing they are working on.
    ["project", "/projects"],
    ["client", "/projects"],
    ["kanban", "/projects"],
    // v1.145.0 — the on-ramp, asked for in the words people actually use.
    ["writing samples", "/train"],
    ["learn my style", "/train"],
    ["import my notes", "/train"],
  ])("%o finds %s", (query, href) => {
    expect(hrefsFor(query)).toContain(href);
  });

  it("matches regardless of how the user cases it", () => {
    expect(hrefsFor("OLLAMA")).toContain("/connections");
    expect(hrefsFor("Redact")).toContain("/documents");
  });

  it("returns nothing for a phrase we genuinely do not cover", () => {
    // Guards the matcher itself: if this ever passes, `matches` has gone loose
    // and every query would "find" something, which is worse than no results.
    expect(hrefsFor("zzzzz-not-a-feature")).toEqual([]);
  });
});

describe("nav catalogue — shape invariants", () => {
  it("has no duplicate hrefs", () => {
    const hrefs = NAV_ENTRIES.map((e) => e.href);
    expect(new Set(hrefs).size).toBe(hrefs.length);
  });

  it("has no duplicate section labels", () => {
    const labels = NAV.map((s) => s.label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("gives every section at least one entry", () => {
    for (const section of NAV) {
      expect(section.items.length).toBeGreaterThan(0);
    }
  });

  it("routes all start with a slash", () => {
    for (const entry of NAV_ENTRIES) {
      expect(entry.href.startsWith("/")).toBe(true);
    }
  });

  it("NAV_ENTRIES is exactly NAV flattened, in nav order", () => {
    // Search results and the rail must agree on order; a divergence here means
    // one of them is reading a stale copy of the catalogue.
    expect(NAV_ENTRIES).toEqual(NAV.flatMap((s) => s.items));
    expect(NAV_ENTRIES[0].href).toBe("/");
  });
});

/**
 * The rail's information architecture, frozen. This catalogue IS the sidebar —
 * Sidebar.tsx renders straight off it — so a reshuffle here silently reorders
 * the rail the user has built muscle memory on, and a stray entry adds a link
 * to it. Without this pin the rest of the suite stays green through both:
 * every other test is per-entry and order-blind.
 *
 * Changing the rail on purpose is fine. Change this list in the same commit.
 */
const RAIL: ReadonlyArray<readonly [string, readonly string[]]> = [
  // v1.151.1: /projects joins the catalogue — it is named a hero surface in
  // nav.ts's own header comment and is the product's context spine, yet it had
  // no rail row, no search entry and no tile until now.
  ["Work", ["/", "/chat", "/terminals", "/projects", "/sessions", "/activity", "/creative"]],
  [
    "Automate",
    [
      "/workflows",
      "/schedules",
      "/templates",
      "/agents",
      "/tools",
      "/autonomy",
      "/sentinels",
      "/computeruse",
      "/webhooks",
      "/reflex",
      "/self-dev",
    ],
  ],
  // v1.144.0: "You" leads Knowledge — the profile injected into every prompt.
  // v1.145.0: "Train on me" follows it — the on-ramp that fills everything else.
  [
    "Knowledge",
    ["/you", "/train", "/memory", "/documents", "/filesearch", "/skills", "/artifacts"],
  ],
  ["Connections", ["/connections", "/fleet", "/secrets", "/channels"]],
  ["System", ["/usage", "/updates", "/settings", "/help"]],
];

describe("nav catalogue — the rail's IA is pinned", () => {
  it("has exactly these sections, in this order", () => {
    expect(NAV.map((s) => s.label)).toEqual(RAIL.map(([label]) => label));
  });

  it.each(RAIL)("section %s holds exactly its routes, in order", (label, hrefs) => {
    const section = NAV.find((s) => s.label === label);
    expect(section).toBeDefined();
    expect(section!.items.map((i) => i.href)).toEqual([...hrefs]);
  });

  it("adds no route the rail does not show", () => {
    // Search mirrors the sidebar IA: routes that exist but are reached from
    // inside another surface (/projects, /marketplace, …) are deliberately
    // absent, and a page cannot be smuggled into the rail via this file.
    expect(NAV_ENTRIES.map((e) => e.href)).toEqual(RAIL.flatMap(([, hrefs]) => [...hrefs]));
  });
});
