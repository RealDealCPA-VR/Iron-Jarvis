/**
 * The IJ harness view keeps the app's calm rules (v1.369.0, IJ harness plan
 * Phase 6). Source pins over every file in components/harness:
 *
 *   - whole-pixel text sizes and theme tokens only: no half-pixel size, no
 *     literal Tailwind hue, no literal colour value (the chat cards' guard,
 *     chat-cards-whole-pixels-v1329, applied to the new folder);
 *   - a terminal the session's Terminal tab holds is HIDDEN behind another
 *     tab, never `display: none` and never unmounted (the v1.190.0 rule: a
 *     terminal in a zero-sized holder wraps its replay for good);
 *   - the full-height view subtracts the demo strip (`--ij-strip-h`), like
 *     every other full-height module;
 *   - a model reply is drawn through the ONE markdown renderer.
 */

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const DIR = path.join(__dirname, "..", "components", "harness");
const read = (f: string) => readFileSync(path.join(DIR, f), "utf8").replace(/\r\n/g, "\n");
const files = readdirSync(DIR).filter((f) => /\.(tsx|ts)$/.test(f)).sort();

const HUES =
  "slate|gray|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const PROPS =
  "text|bg|border|ring|from|via|to|fill|stroke|outline|decoration|divide|placeholder|shadow|caret|accent";
const HALF_PIXEL = /text-\[\d+\.5px\]/g;
const LITERAL_HUE = new RegExp(
  `(?<![\\w-])(?:${PROPS})(?:-[trblxyse])?-(?:${HUES})-\\d{2,3}(?:\\/(?:\\d{1,3}|\\[[0-9.]+\\]))?`,
  "g",
);
const ARBITRARY_COLOUR = new RegExp(
  `(?<![\\w-])(?:${PROPS})-\\[(?:#|(?:rgb|hsl)a?\\((?!\\s*var\\())[^\\]]*\\]`,
  "g",
);

describe("components/harness keeps the calm rules", () => {
  it("scans the real folder", () => {
    for (const f of [
      "IjHarnessView.tsx",
      "HarnessSidebar.tsx",
      "HarnessSession.tsx",
      "HarnessPanes.tsx",
      "HarnessAccounts.tsx",
      "BrowserPreview.tsx",
    ]) {
      expect(files, f).toContain(f);
    }
  });

  it("no half-pixel size, literal hue or literal colour value", () => {
    const hits: string[] = [];
    for (const f of files) {
      const src = read(f);
      for (const re of [HALF_PIXEL, LITERAL_HUE, ARBITRARY_COLOUR]) {
        for (const m of src.matchAll(re)) {
          hits.push(`${f}:${src.slice(0, m.index ?? 0).split("\n").length} ${m[0]}`);
        }
      }
    }
    expect(hits, "use the 11/12/13/14 px scale and the tone-* tokens").toEqual([]);
  });

  it("the patterns catch what they claim", () => {
    const caught = (s: string) => [HALF_PIXEL, LITERAL_HUE, ARBITRARY_COLOUR].some((re) => [...s.matchAll(re)].length > 0);
    for (const bad of ['"text-[11.5px]"', '"text-amber-300"', '"bg-[#0a0c11]"', '"border-emerald-500/30"']) {
      expect(caught(bad), bad).toBe(true);
    }
    for (const good of ['"text-[13px] text-tone-warn"', '"bg-accent/10 text-zinc-400"', '"bg-[rgb(var(--accent-rgb))]"']) {
      expect(caught(good), good).toBe(false);
    }
  });

  it("the session's terminal is hidden behind another tab, never display:none", () => {
    const panes = read("HarnessPanes.tsx");
    expect(panes).toContain('data-testid="pane-terminal-layer"');
    expect(panes).toMatch(/visibility:\s*[^,}]*"hidden"/);
    for (const f of files) {
      expect(read(f), f).not.toMatch(/display:\s*["']none["']|\bhidden\b(?=[^"]*")[^"]*pane-terminal/);
    }
  });

  it("the full-height view subtracts the demo strip", () => {
    expect(read("IjHarnessView.tsx")).toMatch(/h-\[calc\(100vh-[^\]]*-var\(--ij-strip-h,0px\)\)\]/);
  });

  it("a reply is drawn through the one markdown renderer", () => {
    const session = read("HarnessSession.tsx");
    expect(session).toMatch(/from "@\/components\/Markdown"/);
    for (const f of files) expect(read(f), f).not.toMatch(/from "react-markdown"/);
  });
});
