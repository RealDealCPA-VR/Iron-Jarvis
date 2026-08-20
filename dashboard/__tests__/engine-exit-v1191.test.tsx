/**
 * v1.191.0 — "The engine exited (code 4294967295)" becomes a sentence a
 * person can act on.
 *
 * The user's report was that string, verbatim, from the Creative Studio.
 * 4294967295 is -1 in unsigned clothing — the exit a process reads when
 * something killed it from outside, most commonly the app itself restarting
 * underneath the session (an update's restart-to-apply kills the daemon's
 * ConPTY children). The number explained nothing and the state offered no
 * way forward.
 *
 * Two halves: the translation (unit-tested — it is a pure function) and the
 * recovery affordance (source-pinned — the house idiom for page-level seams).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { describeEngineExit } from "@/lib/format";

describe("describeEngineExit", () => {
  it("translates -1 in both of its spellings, keeping the searchable fact", () => {
    for (const code of [4294967295, -1]) {
      const s = describeEngineExit(code);
      expect(s).toContain("terminated");
      expect(s).toContain("-1"); // the raw fact stays searchable
      expect(s).toContain("restarted"); // the most common actual cause, named
      expect(s).not.toContain("4294967295"); // the meaningless spelling goes
    }
  });

  it("keeps the honest boring cases boring", () => {
    expect(describeEngineExit(0)).toContain("normally");
    expect(describeEngineExit(null)).toBe("The engine exited.");
    expect(describeEngineExit(3221225786)).toContain("interrupted");
    // An unknown code is still shown — a translation layer must never
    // swallow the one fact the user could search for.
    expect(describeEngineExit(7)).toContain("(code 7)");
  });
});

describe("the exited state offers the way forward (source-pinned)", () => {
  const page = readFileSync(
    join(process.cwd(), "app", "creative", "page.tsx"),
    "utf8",
  );

  it("renders the translation, not the raw code", () => {
    expect(page).toContain("describeEngineExit(exitCode)");
    // The old one-liner is gone in both of its habits.
    expect(page).not.toMatch(/The engine exited\{exitCode !== null \? ` \(code/);
  });

  it("offers a one-click relaunch of the same setup", () => {
    // v1.192.0 REWRITE: this used to pin `void start()`, which was the defect
    // (finding #21) — start() reads the SETUP FORM, whose folder comes from a
    // single mount-time /fs/list that never retries, so the button silently
    // no-opped whenever that listing had failed, and otherwise relaunched
    // whatever the form currently held rather than "the same engine, same
    // folder" the label promises. The relaunch now reads the dead SESSION.
    expect(page).toMatch(/onRestart=\{isLast \? \(\) => void relaunch\(\) : undefined\}/);
    expect(page).toContain("Start a new session — same engine, same folder");
  });

  it("points at the evidence — the dead terminal's last screen", () => {
    expect(page).toMatch(/terminals\?focus=\$\{encodeURIComponent\(session\.terminalId\)\}/);
  });
});
