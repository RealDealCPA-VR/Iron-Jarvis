/**
 * v1.313.0 — the coordinator's follow-ups to UX wave 1 (files no track owned).
 *
 *  - PageHeader's hint popover is always rendered (opacity 0) so
 *    `aria-describedby` resolves; at `max-w-md` (448px) it widened <main> to
 *    460px on a 390px phone, so most pages panned sideways by 70px. Its width
 *    is now capped by the viewport.
 *  - /updates "Applying runs … → …": bare text nodes in a flex row each became
 *    a flex item, so on a phone the <Code> chips stacked vertically. The
 *    sentence is one inline <span> now.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const read = (rel: string) =>
  readFileSync(join(__dirname, "..", rel), "utf-8").replace(/\r\n/g, "\n");

describe("PageHeader hint popover never widens a phone page", () => {
  it("is capped by the viewport, not a fixed 28rem", () => {
    const s = read("components/PageHeader.tsx");
    const popover = s.match(/className=\{`absolute left-0 top-full[^`]*`/);
    expect(popover, "the hint popover's className").not.toBeNull();
    const cls = popover![0];
    expect(cls).toContain("max-w-[min(28rem,calc(100vw-2rem))]");
    expect(cls.split(/\s+/)).not.toContain("max-w-md");
  });
});

describe("/updates apply sentence flows inline", () => {
  it("wraps the text and <Code> chips in ONE span inside the flex row", () => {
    const s = read("app/updates/page.tsx");
    const i = s.indexOf("Applying runs");
    expect(i).toBeGreaterThan(0);
    const before = s.slice(s.lastIndexOf("<p", i), i);
    // v1.314.0 (UX wave 2): the exact commands moved one disclosure down
    // ("The exact steps"), out of the flex row; they must still flow inline.
    expect(s.slice(s.lastIndexOf("<details", i), i)).toContain("The exact steps");
    // The tag right before the sentence is a <span>, not the icon or the <p>.
    expect(before.trimEnd().endsWith("<span>"), "sentence must start inside a <span>").toBe(true);
    const after = s.slice(i, s.indexOf("</p>", i));
    // v1.316.0: each chip may carry a className (whitespace-nowrap).
    expect(after).toMatch(/<Code(?: className="[^"]*")?>pnpm build<\/Code>/);
    expect(after.trimEnd().endsWith("</span>")).toBe(true);
  });
});
