/**
 * Readiness 12A — the 0d.6 engine-room words still on a typical user's path,
 * and two 0f.3 leftovers.
 *
 *  - a model list named a fleet node's group by its raw id ("fleet-spark ·
 *    Spark B") when the caller handed the pick without its row's name;
 *  - a scheduled workflow stopped mid-run read "ok — workflow ran" (its row
 *    is the REAL daemon's, fixtures/cancelled-scheduled-workflow-v1387.json);
 *  - the Tools page said extensions add "files" (built in, per the
 *    Directory), Sessions said "No orphaned worktrees to prune.", the
 *    Schedules / project rows said "open session", and Help's catch-all sent
 *    the user to "the System health card on the Overview (switch on
 *    Advanced…)", a card and a switch the calm UI retired.
 *
 * The copy pins read the source (CRLF-normalised at the reader).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { modelGroupName, modelPickWords } from "@/lib/onboarding";
import { scheduleDetail, scheduleStatusWords } from "@/lib/scheduleOutcome";

const DASH = join(__dirname, "..");
const read = (p: string) => readFileSync(join(DASH, p), "utf-8").replace(/\r\n/g, "\n");
const FIX = JSON.parse(read("__tests__/fixtures/cancelled-scheduled-workflow-v1387.json"));

/** The lines a person can read: comments dropped (a pin on copy, not on notes). */
function prose(src: string): string {
  return src
    .split("\n")
    .filter((l) => !/^\s*(\/\/|\*|\/\*|\{\/\*)/.test(l))
    .join("\n");
}

describe("0f.3: a model list names a fleet node's group as the machine, never its id", () => {
  const CATALOG = [{ provider: "fleet-spark", model: "qwen3", name: "Spark B" }];

  it("with the catalog: the machine's name", () => {
    expect(modelPickWords({ provider: "fleet-spark", model: "qwen3" }, CATALOG)).toBe("Spark B · qwen3");
    // The server alias names the machine once.
    expect(modelPickWords({ provider: "fleet-spark", model: "fleet" }, CATALOG)).toBe("Spark B");
  });

  it("without it: 'Your model server', never 'fleet-spark'", () => {
    const words = modelPickWords({ provider: "fleet-spark", model: "qwen3" });
    expect(words).toBe("Your model server · qwen3");
    expect(words).not.toContain("fleet-spark");
    expect(modelGroupName({ provider: "fleet-spark", model: "qwen3" })).toBe("Your model server");
  });

  it("nothing else changed: a named row keeps its name; a cloud provider its own", () => {
    expect(modelGroupName({ provider: "fleet-spark", model: "qwen3", name: "Spark B" })).toBe("Spark B");
    expect(modelPickWords({ provider: "claude-cli", model: "subscription" })).toBe("Your Claude plan");
    expect(modelPickWords({ provider: "openai", model: "gpt-x" })).toMatch(/^OpenAI · /);
  });
});

describe("0f.3: a scheduled workflow stopped mid-run reads Stopped (the real daemon's row)", () => {
  it("never 'ok — workflow ran'", () => {
    expect(FIX.row.last_status).toBe("cancelled");
    expect(scheduleStatusWords(FIX.row.last_status).label).toBe("Stopped");
    expect(scheduleDetail(FIX.row.last_detail)).toBe("Your workflow “nightly-tidy” was stopped before it finished.");
    expect(scheduleDetail(FIX.row.last_detail)).not.toMatch(/ ran$/);
  });
});

describe("0d.6: engine-room words on a typical user's path", () => {
  it("Tools: files and folders are built in; extensions are not 'files'", () => {
    const src = read("components/settings/pages/ToolsPage.tsx");
    expect(src).not.toMatch(/bigger abilities/);
    expect(prose(src)).toMatch(/files and folders\s+included/);
    expect(prose(src)).toMatch(/files and folders are\s+built in/);
  });

  it("Sessions' clean-up says leftover folders, never orphaned worktrees", () => {
    const src = prose(read("app/sessions/page.tsx"));
    expect(src).not.toMatch(/orphaned worktree|to prune|Pruned \$/);
    expect(src).toContain("No leftover folders to clean up.");
    expect(src).not.toMatch(/git worktrees left behind/);
  });

  it("no row says 'open session' (a run is opened)", () => {
    for (const p of [
      "app/schedules/page.tsx",
      "components/project/ProjectSchedules.tsx",
      "components/project/ProjectTasks.tsx",
      "components/kanban/SessionCard.tsx",
      "components/TimeTravelFeed.tsx",
    ]) {
      expect(prose(read(p)), p).not.toMatch(/\bopen session\b/i);
    }
  });

  it("Help's catch-all points at Everything › Status, never a retired card or switch", () => {
    const src = prose(read("app/help/page.tsx"));
    expect(src).not.toMatch(/System health card/);
    expect(src).toMatch(/Everything › Status/);
  });
});
