/**
 * v1.532.0 (fourth review OT5): the Connect words said "Nothing leaves this
 * PC", but what a connected app looks up becomes part of that app's chat,
 * which goes to its own model. The words now say so, for each app, and say
 * what is never shared (client work and Local only work: the daemon's outside
 * wall, tests/test_fourth_review_outside_wall_v1532.py).
 */
import { describe, expect, it } from "vitest";

import { CONNECT_WORDS } from "@/lib/otherTools";

describe("the Connect words are honest about what leaves this PC", () => {
  const apps = ["claude-desktop", "codex", "claude-code"];

  it("covers every app", () => {
    expect(Object.keys(CONNECT_WORDS).sort()).toEqual([...apps].sort());
  });

  it.each(apps)("%s: says what goes out and what never does", (app) => {
    const words = CONNECT_WORDS[app]!;
    expect(words).not.toMatch(/nothing leaves this pc/i);
    expect(words).toMatch(/goes to (Claude's|Codex's) model/);
    expect(words).toContain("client work and Local only work are never shared");
    expect(words).toContain("titles, folders, file names, short summaries");
  });
});
