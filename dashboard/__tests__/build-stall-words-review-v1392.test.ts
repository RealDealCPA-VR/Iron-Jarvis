/**
 * v1.392.0 review — every NEW sentence Phase 3 put in front of a person names
 * a session of Build's coding view the user's way: "coding session"
 * (lib/buildWords, v1.389.0). The doer's bell headline said "Your Build
 * session has stopped making progress"; VOCABULARY.md keeps "Build session"
 * only where an OLDER fixture holds it equal. The daemon's twin
 * (comm/notifier.BUILD_STALLED_HEADLINE) and the tray (desktop/main.js) are
 * held to the same fixture row (tests/test_build_stall_review_v1392.py,
 * tests/test_one_notice_v13600.py).
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { BUILD_STALLED_HEADLINE, buildStalledBody } from "@/lib/buildLinks";
import { CODING_SESSION } from "@/lib/buildWords";

interface Case {
  case: string;
  title?: string;
}

function fixtureCase(name: string): Case {
  const file = path.join(__dirname, "fixtures", "one-notice-v13600.json");
  const cases = (JSON.parse(readFileSync(file, "utf-8").replace(/\r\n/g, "\n")) as { cases: Case[] }).cases;
  return cases.find((c) => c.case === name)!;
}

describe("the stall notice's words (v1.392.0 review)", () => {
  it("names the session the user's way, from the one constant", () => {
    expect(BUILD_STALLED_HEADLINE).toBe(`Your ${CODING_SESSION} has stopped making progress`);
    expect(BUILD_STALLED_HEADLINE).toBe("Your coding session has stopped making progress");
    expect(BUILD_STALLED_HEADLINE).not.toMatch(/Build session/);
  });

  it("is the one-notice fixture's row, which the daemon and the tray say too", () => {
    expect(fixtureCase("build-turn-stalled").title).toBe(BUILD_STALLED_HEADLINE);
  });

  it("keeps the body to the title and the daemon's own sentence", () => {
    expect(buildStalledBody("Port the parser", "No progress for 10 minutes — waiting on Codex's answer")).toBe(
      "Port the parser · No progress for 10 minutes — waiting on Codex's answer",
    );
  });
});
