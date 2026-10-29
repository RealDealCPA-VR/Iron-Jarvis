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
 *
 * v1.406.0 (part 1, cases MOVED ON PURPOSE): the coding sessions moved to the
 * chat page's Chat | Code switch. The view is "Code" (CODING_VIEW_NAME), its
 * list "Coding sessions" (CODING_SESSIONS_LIST); the switch lives in
 * components/chat/ChatCodeSurface; Build's line is the terminals only and the
 * old names find Chat.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import {
  BUILD_BLURB,
  CHAT_BLURB,
  CHAT_VIEW_NAME,
  CODE_VIEW_NAME,
  CODING_SESSION,
  CODING_SESSIONS_LIST,
  CODING_SESSION_LABEL,
  CODING_VIEW_NAME,
} from "@/lib/buildWords";
import { NAV_ENTRIES } from "@/lib/nav";
import { ContextMeter } from "@/components/harness/ContextMeter";
import { COMPACTION_OFFER_BUTTON } from "@/lib/chatControlWords";
import { toActivity } from "@/components/NotificationBell";

const DASH = process.cwd();
const read = (...p: string[]) => readFileSync(join(DASH, ...p), "utf8").replace(/\r\n/g, "\n");

describe("one name, one place", () => {
  it("the user's names: Chat | Code; the list is Coding sessions; one of them a coding session", () => {
    expect(CHAT_VIEW_NAME).toBe("Chat");
    expect(CODE_VIEW_NAME).toBe("Code");
    expect(CODING_VIEW_NAME).toBe(CODE_VIEW_NAME);
    expect(CODING_SESSIONS_LIST).toBe("Coding sessions");
    expect(CODING_SESSION_LABEL).toBe("Coding session");
    expect(CODING_SESSION).toBe("coding session");
  });

  it("every surface that names the view READS the constant (none spells it)", () => {
    const readers: [string[], string][] = [
      [["app", "terminals", "page.tsx"], "CODING_SESSIONS_LIST"],
      [["components", "chat", "ChatCodeSurface.tsx"], "CODE_VIEW_NAME"],
      [["components", "harness", "HarnessSidebar.tsx"], "CODING_SESSIONS_LIST"],
      [["components", "AppSidebar.tsx"], "CODING_SESSIONS_LIST"],
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
  // v1.409.0 (moved ON PURPOSE): Build has its own switch now, Code |
  // Terminal (the real Claude Code / Codex in a window), through the SAME
  // component as the chat page's.
  it("the switch reads 'Chat | Code' on the chat page, and 'Code | Terminal' on Build", () => {
    const surface = read("components", "chat", "ChatCodeSurface.tsx");
    expect(surface).toContain('{ value: "chat", label: CHAT_VIEW_NAME,');
    expect(surface).toContain('{ value: "code", label: CODE_VIEW_NAME,');
    const build = read("app", "terminals", "page.tsx");
    expect(build).toContain('{ value: "code", label: BUILD_CODE_VIEW_NAME,');
    expect(build).toContain('{ value: "terminal", label: BUILD_TERMINAL_VIEW_NAME,');
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

  it("Build's one line is the terminals; the coding sessions' old names find Chat", () => {
    const build = NAV_ENTRIES.find((e) => e.href === "/terminals")!;
    expect(build.blurb).toBe(BUILD_BLURB);
    expect(BUILD_BLURB).toMatch(/^Terminals for your own coding tools /);
    expect(BUILD_BLURB).not.toMatch(/coding session/i);
    expect(build.aliases).toContain("pane");
    const chat = NAV_ENTRIES.find((e) => e.href === "/chat")!;
    expect(chat.blurb).toBe(CHAT_BLURB);
    for (const alias of ["IJ harness", "harness", "coding session"]) {
      expect(chat.aliases).toContain(alias);
      expect(build.aliases).not.toContain(alias);
    }
  });

  it("Help has an entry for the view, and its Build card uses the same line", () => {
    const help = read("app", "help", "page.tsx");
    expect(help).toContain("term: CODING_VIEW_NAME,");
    expect(help).toContain("desc: BUILD_BLURB,");
  });
});
