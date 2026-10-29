/**
 * v1.369.0 review — on a phone the session's conversation is ONE screen-sized
 * unit (header, transcript, composer) and its panes come AFTER it.
 *
 * Below lg the conversation box carried `flex-1` inside a column with no
 * height of its own, so its height became its content's: a short session
 * squeezed the transcript to a sliver above the panes (388 px of a 658 px
 * box at 390x844, measured), and a long one grew the page instead of
 * scrolling inside. It must not flex below lg; from lg it fills the row.
 *
 * jsdom has no layout, so this pins the classes; the real measurement is in
 * e2e/ij-harness.spec.ts ("390 px").
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const src = readFileSync(join(process.cwd(), "components", "harness", "HarnessSession.tsx"), "utf8").replace(
  /\r\n/g,
  "\n",
);

function classOf(testId: string): string {
  const at = src.indexOf(`data-testid="${testId}"`);
  expect(at, `${testId} is in HarnessSession`).toBeGreaterThan(-1);
  const m = /className="([^"]*)"/.exec(src.slice(at, at + 600));
  expect(m, `${testId} has a className`).not.toBeNull();
  return m![1];
}

describe("the phone conversation box", () => {
  it("is a fixed screen-sized box below xl and fills the row from xl", () => {
    const tokens = classOf("harness-conversation").split(/\s+/);
    expect(tokens).toContain("h-[78vh]");
    expect(tokens).toContain("flex-none");
    expect(tokens).not.toContain("flex-1");
    expect(tokens).toContain("xl:flex-1");
    expect(tokens).toContain("xl:h-auto");
  });

  it("goes side by side only from xl, in BOTH rows (v1.370.1)", () => {
    // From lg, a 1024 px window gave the conversation ~90 px beside the
    // app's sidebar, the session list and the panes (the user's report).
    const view = readFileSync(join(process.cwd(), "components", "harness", "IjHarnessView.tsx"), "utf8");
    for (const [name, text] of [["HarnessSession", src], ["IjHarnessView", view]] as const) {
      expect(text, name).not.toMatch(/lg:(flex-row|flex-1|w-\[|w-60|h-auto|min-h-0|max-h-none|h-\[calc)/);
      expect(text, name).toContain("xl:flex-row");
    }
  });

  it("holds the composer, and the panes come after it", () => {
    const conv = src.indexOf('data-testid="harness-conversation"');
    const composer = src.indexOf('data-testid="harness-composer"');
    // v1.406.0 part 7 (moved on purpose): the panes are built once as
    // `panesEl` (a split draws them in its shared pane column); the session's
    // own aside that draws them still comes after the conversation.
    expect(src.indexOf("const panesEl = (\n    <HarnessPanes")).toBeGreaterThan(-1);
    const panes = src.indexOf("{panesEl}", conv);
    const close = src.indexOf("</section>", conv);
    expect(conv).toBeLessThan(composer);
    expect(composer).toBeLessThan(close);
    expect(close).toBeLessThan(panes);
  });
});
