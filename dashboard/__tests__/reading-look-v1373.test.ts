/**
 * v1.373.0 — the user's report: a Build session's reply in the IJ harness ran
 * edge to edge in small, tight text beside Claude's own app, whose spacing and
 * font they preferred; they chose to ship Inter app-wide.
 *
 * Pinned here:
 *  - the app's font is BUNDLED (the theme named Inter from the start, but no
 *    file shipped, so Windows drew Segoe UI): the package is pinned exactly,
 *    the layout imports it, and the theme names its face first;
 *  - the harness conversation reads in ONE centred column with 15 px text,
 *    and the narrow Build-pane chat keeps its own compact look.
 *
 * Source pins read files with CRLF normalised (the v1.232.1 rule).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  HARNESS_COLUMN,
  HARNESS_COMPOSER_BOX,
  HARNESS_REPLY_PROSE,
  HARNESS_TRANSCRIPT_ROWS,
  HARNESS_USER_BUBBLE,
} from "@/components/harness/harnessLook";
import { PANE_REPLY_PROSE } from "@/components/terminal/paneChatLook";

const root = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(root, rel), "utf-8").replace(/\r\n/g, "\n");

describe("the app's font is shipped with it", () => {
  it("the Inter package is pinned to an exact version", () => {
    const pkg = JSON.parse(read("package.json"));
    expect(pkg.dependencies["@fontsource-variable/inter"]).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("the root layout imports the face, upright and italic, before the app's own CSS", () => {
    const layout = read("app/layout.tsx");
    const font = layout.indexOf('import "@fontsource-variable/inter";');
    const italic = layout.indexOf('import "@fontsource-variable/inter/wght-italic.css";');
    const css = layout.indexOf('import "./globals.css";');
    expect(font).toBeGreaterThan(-1);
    expect(italic).toBeGreaterThan(font);
    expect(css).toBeGreaterThan(italic);
  });

  it("the theme names the bundled face first", () => {
    const tw = read("tailwind.config.ts");
    const sans = tw.slice(tw.indexOf("sans: ["));
    expect(sans.indexOf('"Inter Variable"')).toBeGreaterThan(-1);
    expect(sans.indexOf('"Inter Variable"')).toBeLessThan(sans.indexOf('"Segoe UI"'));
  });
});

describe("the IJ harness reads like a document", () => {
  it("one centred column the chat page's width, with 15 px text and room between lines", () => {
    expect(HARNESS_TRANSCRIPT_ROWS).toContain("[&>*]:max-w-[760px]");
    expect(HARNESS_TRANSCRIPT_ROWS).toContain("[&>*]:mx-auto");
    // The box's wrapper carries 4 px padding either side: 768 = 760 + 8.
    expect(HARNESS_COLUMN).toContain("max-w-[768px]");
    expect(HARNESS_COLUMN).toContain("mx-auto");
    for (const cls of [HARNESS_REPLY_PROSE, HARNESS_USER_BUBBLE, HARNESS_COMPOSER_BOX]) {
      expect(cls).toContain("text-[15px]");
    }
    // The Markdown renderer's own spacing is out-ranked inside a reply.
    for (const rule of ["[&_p]:my-3", "[&_p]:leading-[1.7]", "[&_li]:leading-[1.7]", "[&_ul]:space-y-1.5"]) {
      expect(HARNESS_REPLY_PROSE).toContain(rule);
    }
  });

  it("the session screen uses the harness look, never the pane's, for the conversation", () => {
    const src = read("components/harness/HarnessSession.tsx");
    expect(src).toContain('data-testid="harness-live-text" className={HARNESS_REPLY_PROSE}');
    expect(src).toContain('data-testid="harness-user-bubble" className={HARNESS_USER_BUBBLE}');
    expect(src).toMatch(/data-testid="harness-transcript"\s+className=\{`[^`]*\$\{HARNESS_TRANSCRIPT_ROWS\}`\}/);
    expect(src).toContain("<div className={`shrink-0 px-1 pb-1 ${HARNESS_COLUMN}`}>");
    expect(src).toContain("className={HARNESS_COMPOSER_BOX}");
    for (const old of ["className={PANE_REPLY_PROSE}", "className={PANE_USER_BUBBLE}", "className={PANE_COMPOSER_BOX}"]) {
      expect(src).not.toContain(old);
    }
  });

  it("the narrow Build-pane chat keeps its compact look", () => {
    expect(PANE_REPLY_PROSE).toContain("text-sm");
    expect(PANE_REPLY_PROSE).not.toContain("text-[15px]");
  });
});
