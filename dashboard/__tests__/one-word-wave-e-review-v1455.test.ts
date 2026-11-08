import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * v1.455.0 (Wave E) review: words the wave wrote that the code does not make
 * true. Each pin reads the SOURCE (LF-normalised: CI checks out with CRLF).
 */
const read = (rel: string) => readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");

describe("the Rules page says who does the task", () => {
  it("a rule's agent action is always Jarvis (reflex/router.py runs AgentType.SUPERVISOR), never 'one of your agents'", () => {
    const page = read("app/reflex/page.tsx");
    // reflex/router._run_session gives the task to the SUPERVISOR: the user
    // never picks which agent, so "one of your agents" promised a choice.
    expect(page).not.toMatch(/one of your agents/i);
    expect(page).toContain("or Jarvis on this PC, automatically.");
    expect(page).toMatch(/agent — Jarvis takes the task\s+on this PC/);
  });
});

describe("a sign-in press opens a terminal in Build, never a window", () => {
  it("every surface that offers the press says so (the link after it opens Build)", () => {
    for (const rel of [
      "components/connections/CliSignInPress.tsx",
      "components/onboarding/ConnectDoors.tsx",
      "app/chat/page.tsx",
    ]) {
      const src = read(rel);
      expect(src, rel).not.toMatch(/A window opened/);
      expect(src, rel).toMatch(/A terminal opened in Build with \{[\w.]+\} running\./);
    }
  });
});
