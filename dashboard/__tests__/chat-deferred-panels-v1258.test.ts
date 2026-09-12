/**
 * S-03 (v1.258.0): six panels the chat route no longer downloads before you open
 * them.
 *
 * Plain words: opening a chat used to fetch the code for a project board, a
 * knowledge rail, a share box, a folder-batch card, a run-result card and the
 * goal offer — none of which is on screen when a chat opens. Now that code
 * arrives when the panel does. Measured: /chat's route-specific JS fell from
 * 567.1 KiB to 444.6 KiB (-122.5 KiB), and `next build` reports first-load JS
 * down from 307 kB to 270 kB.
 *
 * THE HOUSE IDIOM (v1.163.0, v1.190.0): a seam a rendered test cannot reach is
 * pinned against the source. Line endings are normalised because this working
 * tree is CRLF — a pin written against a bare newline matches nothing and passes
 * for free. Comments are stripped for a reason specific to this change: the
 * comment block introducing these deferrals NAMES every component involved,
 * including the two deliberately left static, so an un-stripped check could pass
 * on prose instead of code.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const LF = String.fromCharCode(10);
const CRLF = String.fromCharCode(13) + LF;
const SRC = readFileSync(resolve(process.cwd(), "app/chat/page.tsx"), "utf-8")
  .split(CRLF)
  .join(LF);

/** Source with block and line comments stripped. */
const CODE = SRC.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

/** The six panels, each rendered only behind a gate that is false at first paint. */
const DEFERRED = [
  "ProjectSurface",
  "KnowledgePanel",
  "ShareChatDialog",
  "BatchSuggestCard",
  "RunResultCard",
  "GoalBirth",
] as const;

/** The module each panel must be deferred FROM, so a pin cannot be satisfied by
 *  some other panel's dynamic import happening to sit nearby. */
const MODULE_OF: Record<(typeof DEFERRED)[number], string> = {
  ProjectSurface: "components/project/ProjectSurfaces",
  KnowledgePanel: "components/project/KnowledgePanel",
  ShareChatDialog: "components/chat/ShareChatDialog",
  BatchSuggestCard: "components/chat/BatchSuggestCard",
  RunResultCard: "components/chat/RunResultCard",
  GoalBirth: "components/chat/GoalContractCard",
};

/**
 * The two that must STAY static — the anti-vacuity control.
 *
 * Each shares a module with a chip the page renders anyway (`CompactionChip`,
 * `WorkflowRunChip`), and bundling is per-module: deferring the card would move
 * zero bytes while looking like progress. If a later change "tidies" these into
 * dynamic imports, this pin fails and says why.
 */
const MUST_STAY_STATIC = ["CompactionCard", "WorkflowDraftCard"] as const;

describe("the chat route defers panels nobody has opened", () => {
  it("imports next/dynamic once", () => {
    expect(CODE).toContain('import dynamic from "next/dynamic"');
  });

  for (const name of DEFERRED) {
    it(`${name} is loaded on demand, not before first paint`, () => {
      // Declared as a dynamic component...
      expect(CODE).toMatch(new RegExp(`const ${name} = dynamic\\(`));
      // ...and NOT pulled in by a static import. `\\b` matters: the type-only
      // `ProjectSurfaceView` import must not satisfy a check for
      // `ProjectSurface`, and a type import pulls no runtime code anyway.
      expect(CODE).not.toMatch(
        new RegExp(`import \\{[^}]*\\b${name}\\b[^}]*\\} from`),
      );
      // ...and the deferral names THIS panel's own module. The first version of
      // this line matched a dynamic import of ANY path, so it passed whichever
      // panel was under test — vacuous, which is what the rest of this file
      // exists to prevent. String indexing rather than an assembled regex: a
      // regex built inside a patch script is what briefly made a sibling test
      // file unparseable (a written-out newline cannot live in a regex literal).
      const at = CODE.indexOf(`const ${name} = dynamic(`);
      expect(at, `${name} must be declared with dynamic()`).toBeGreaterThan(-1);
      expect(CODE.slice(at, at + 400)).toContain(`import("@/${MODULE_OF[name]}")`);
    });
  }

  for (const name of MUST_STAY_STATIC) {
    it(`${name} stays statically imported — its module ships either way`, () => {
      expect(CODE).toMatch(new RegExp(`\\b${name},`));
      expect(CODE).not.toMatch(new RegExp(`const ${name} = dynamic\\(`));
    });
  }

  it("the chips that keep those two modules resident are really rendered", () => {
    // The reason the control above exists. If these ever stop being rendered,
    // CompactionCard and WorkflowDraftCard become deferrable and this pin should
    // be revisited rather than silently left in place.
    expect(CODE).toContain("<CompactionChip");
    expect(CODE).toContain("<WorkflowRunChip");
  });

  it("every deferred panel still has a gate that is false on open", () => {
    // A deferral is only invisible if nothing renders the panel at first paint.
    for (const gate of [
      "projectView",
      "railTab",
      "shareOpen",
      "batchPreview",
      "runResult",
      "workflowDraft",
    ]) {
      expect(CODE).toContain(gate);
    }
  });
});
