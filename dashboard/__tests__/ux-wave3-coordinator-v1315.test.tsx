/**
 * v1.315.0 — the coordinator's follow-ups to UX wave 3.
 *  - File search's "Searching:" line must not claim a folder in "By meaning"
 *    mode: filesearch/service.search_semantic ignores the roots.
 *  - A project's Recent runs row reads a task through plainText (a delegation
 *    or schedule task can be model-written markdown — the v1.230.0 rule).
 *  - The Files preview (lazy-loaded by chat and Build) is the shared <Modal>.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const read = (rel: string) =>
  readFileSync(join(process.cwd(), rel), "utf-8").replace(/\r\n/g, "\n");

describe("wave 3 coordinator pins", () => {
  it("File search says what 'By meaning' really searches", () => {
    const s = read("app/filesearch/page.tsx");
    expect(s).toContain('{mode === "semantic" ? "the folders Iron Jarvis has indexed" : rootLabel}');
  });

  it("Recent runs reads the task through plainText", () => {
    expect(read("components/project/ProjectTasks.tsx")).toContain(
      'const ask = s.task ? plainText(runAsk(s.task)) : "";',
    );
  });

  it("the Files preview is the shared Modal at layer 80, not a hand-rolled overlay", () => {
    const s = read("components/terminal/FilesPanel.tsx");
    expect(s).toContain('<Modal z={80} label={file.name} onClose={onClose} className="w-full max-w-3xl">');
    expect(s).not.toContain("fixed inset-0 z-[80]");
  });
});
