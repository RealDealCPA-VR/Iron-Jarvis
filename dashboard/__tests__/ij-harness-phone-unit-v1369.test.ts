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
  it("is a fixed screen-sized box below lg and fills the row from lg", () => {
    const tokens = classOf("harness-conversation").split(/\s+/);
    expect(tokens).toContain("h-[78vh]");
    expect(tokens).toContain("flex-none");
    expect(tokens).not.toContain("flex-1");
    expect(tokens).toContain("lg:flex-1");
    expect(tokens).toContain("lg:h-auto");
  });

  it("holds the composer, and the panes come after it", () => {
    const conv = src.indexOf('data-testid="harness-conversation"');
    const composer = src.indexOf('data-testid="harness-composer"');
    const panes = src.indexOf("<HarnessPanes");
    const close = src.indexOf("</section>", conv);
    expect(conv).toBeLessThan(composer);
    expect(composer).toBeLessThan(close);
    expect(close).toBeLessThan(panes);
  });
});
