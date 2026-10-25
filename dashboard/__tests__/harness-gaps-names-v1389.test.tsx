/**
 * Harness gaps, fix 4 — an everyday name for Build's coding-agent view.
 *
 * The v1.388.0 re-audit (0g.6, I7, H10, M1/M2): "IJ harness" — the engine
 * room's word — labelled one of Build's two views, its sidebar, a bell row
 * ("A change to a harness file is suggested") and two terminal refusals
 * ("relaunch the harness"); the harness said "Compact" where chat says
 * "Summarize"; and Build's blurbs described only the terminals, with no Help
 * entry for the view at all.
 *
 *  - the name lives in ONE constant (lib/buildWords) that every surface
 *    reads: no surface spells it, so the user's choice is a one-line change;
 *  - the view switch reads "Terminals | Coding sessions", the sidebar is
 *    named so, the bell's layer-edit row has no "harness";
 *  - the context meter's press says the chat's word, "Summarize";
 *  - Build's blurb (Ctrl K, Everything, Help) names both views, and Help has
 *    an entry for the view;
 *  - "IJ harness" and "harness" still find Build in search.
 *
 * The scanners (vocabulary.test.ts, the two Python scans) carry "harness" as
 * a retired word now.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { BUILD_BLURB, CODING_SESSION, CODING_SESSION_LABEL, CODING_VIEW_NAME } from "@/lib/buildWords";
import { NAV_ENTRIES } from "@/lib/nav";
import { ContextMeter } from "@/components/harness/ContextMeter";
import { COMPACTION_OFFER_BUTTON } from "@/lib/chatControlWords";
import { toActivity } from "@/components/NotificationBell";

const DASH = process.cwd();
const read = (...p: string[]) => readFileSync(join(DASH, ...p), "utf8").replace(/\r\n/g, "\n");

describe("one name, one place", () => {
  it("the user's name: Coding sessions; one of them is a coding session", () => {
    expect(CODING_VIEW_NAME).toBe("Coding sessions");
    expect(CODING_SESSION_LABEL).toBe("Coding session");
    expect(CODING_SESSION).toBe("coding session");
  });

  it("every surface that names the view READS the constant (none spells it)", () => {
    const readers: [string[], string][] = [
      [["app", "terminals", "page.tsx"], "CODING_VIEW_NAME"],
      [["components", "harness", "HarnessSidebar.tsx"], "CODING_VIEW_NAME"],
      [["components", "NotificationBell.tsx"], "CODING_SESSION"],
      [["lib", "runningNow.ts"], "CODING_SESSION_LABEL"],
      [["lib", "nav.ts"], "BUILD_BLURB"],
      [["app", "help", "page.tsx"], "CODING_VIEW_NAME"],
    ];
    for (const [path, name] of readers) {
      const src = read(...path);
      expect(src, path.join("/")).toMatch(
        new RegExp(`import \\{[^}]*\\b${name}\\b[^}]*\\} from "(?:@/lib|\\.)/buildWords"`),
      );
      // The words themselves are never typed again (a rename is one line).
      expect(src, path.join("/")).not.toMatch(/"Coding sessions?"/);
      // The old name is gone from what a user reads (lib/nav.ts keeps it as
      // a search ALIAS on purpose — VOCABULARY.md's rename rule).
      if (path.join("/") !== "lib/nav.ts") expect(src, path.join("/")).not.toMatch(/"IJ harness"|IJ harness sessions/);
    }
  });
});

describe("the surfaces say it", () => {
  it("the view switch reads 'Terminals | Coding sessions'", () => {
    const page = read("app", "terminals", "page.tsx");
    expect(page).toContain('{ value: "harness", label: CODING_VIEW_NAME,');
    expect(page).toContain('{ value: "rail", label: "Terminals",');
  });

  it("the bell's layer-edit row has no engine-room word", () => {
    const row = toActivity({
      id: "e2",
      ts: "2026-10-25T10:00:00Z",
      type: "library.proposal",
      session_id: "build:s1",
      payload: { id: "lib_2", kind: "layer_edit", folder_name: "shop", thread_id: "s1", summary: "memory.md" },
    } as never);
    expect(row!.title).toBe("A change to the coding session instructions is suggested for shop");
    expect(row!.title).not.toMatch(/harness/i);
  });

  it("the context meter's press says the chat's word, Summarize", () => {
    render(
      <ContextMeter
        reading={{ percent: 81, used: 81_000, window: 100_000, tokens: 81_000, level: "suggest" } as never}
        offer
        summarized={false}
        busy={false}
        onCompact={() => {}}
        onViewSummary={() => {}}
      />,
    );
    expect(screen.getByTestId("harness-compact").textContent).toBe(COMPACTION_OFFER_BUTTON);
    expect(COMPACTION_OFFER_BUTTON).toBe("Summarize");
  });

  it("Build's one line names both views, and the old names still find Build", () => {
    const build = NAV_ENTRIES.find((e) => e.href === "/terminals")!;
    expect(build.blurb).toBe(BUILD_BLURB);
    expect(BUILD_BLURB).toMatch(/^Terminals for your own coding tools, and coding sessions /);
    for (const alias of ["IJ harness", "harness", "coding session", "pane"]) expect(build.aliases).toContain(alias);
  });

  it("Help has an entry for the view, and its Build card uses the same line", () => {
    const help = read("app", "help", "page.tsx");
    expect(help).toContain("term: CODING_VIEW_NAME,");
    expect(help).toContain("desc: BUILD_BLURB,");
  });
});
