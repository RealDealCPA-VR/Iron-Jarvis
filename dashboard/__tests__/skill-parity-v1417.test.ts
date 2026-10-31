/**
 * v1.417.0 — SKILL PARITY in the coding session's "/" menu.
 *
 * The daemon now marks each skill with Claude Code's own switches and lists a
 * session FOLDER's own skills (.claude/skills, .agents/skills …, from its
 * folder up to the repository root) on the session's list as `kind:
 * "project"` (tests/test_skill_parity_v1417.py pins the daemon side). Here:
 *
 *  - `user-invocable: false` (`user_invocable: false`) is never a "/" row —
 *    Claude Code hides it from its own menu;
 *  - a folder's own skill runs as one of YOUR skills (`skill`, which the lane
 *    finds in the folder) — never as a harness-layer skill (`folder_skill`,
 *    which only reads .ironjarvis/harness/skills and would 404);
 *  - each row says where it came from in words.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { decodeSkills, pickedOf, skillFields, slashRows, sourceWords } from "@/lib/buildComposer";

describe("the '/' menu honours the harnesses' own switches", () => {
  it("never offers a skill whose SKILL.md says user-invocable: false", () => {
    const yours = decodeSkills(
      {
        skills: [
          { name: "shown", description: "a", source: "claude", user_invocable: true },
          { name: "hidden", description: "b", source: "claude", user_invocable: false },
          { name: "older-daemon", description: "c", source: "codex" },
        ],
      },
      "yours",
    );
    expect(yours.map((s) => s.name)).toEqual(["shown", "older-daemon"]);
  });

  it("runs a folder's own skill as one of yours, said as this folder's", () => {
    const folder = decodeSkills(
      {
        skills: [
          { name: "lint-rules", description: "harness layer", rel: ".ironjarvis/harness/skills/lint-rules/SKILL.md" },
          { name: "deploy-notes", description: "from .claude/skills", kind: "project" },
          { name: "quiet", description: "hidden", kind: "project", user_invocable: false },
        ],
      },
      "folder",
    );
    expect(folder.map((s) => [s.name, s.source, s.origin])).toEqual([
      ["lint-rules", "folder", undefined],
      ["deploy-notes", "yours", "project"],
    ]);
    expect(skillFields(pickedOf(folder[0]))).toEqual({ folder_skill: "lint-rules" });
    expect(skillFields(pickedOf(folder[1]))).toEqual({ skill: "deploy-notes" });
    const rows = slashRows("deploy", [], folder, []);
    expect(rows).toHaveLength(1);
    expect(sourceWords(rows[0])).toBe("this folder");
  });

  it("the chat page's '/' and '+' skill lists leave user-invocable: false out too", () => {
    const src = readFileSync(join(__dirname, "..", "app", "chat", "page.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(src).toContain("return (list ?? []).filter((s) => s.user_invocable !== false);");
    // Both readers of GET /skills go through it — none sets the raw list.
    expect(src.match(/setSkills\(slashSkills\(d\.skills\)\)/g)).toHaveLength(2);
    expect(src).not.toContain("setSkills(d.skills ?? [])");
  });

  it("names the other harnesses' skill folders", () => {
    const yours = decodeSkills(
      {
        skills: ["agents", "grok", "opencode", "pi"].map((origin) => ({
          name: `s-${origin}`,
          description: "",
          source: origin,
        })),
      },
      "yours",
    );
    const words = slashRows("", [], [], yours).map((r) => sourceWords(r));
    expect(words).toEqual(["from your agent skills", "from Grok", "from OpenCode", "from Pi"]);
  });
});
