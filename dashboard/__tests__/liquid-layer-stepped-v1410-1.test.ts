import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * v1.410.1: the ambient liquid layer (`body::before`) drifts in STEPS.
 *
 * Measured in headed Edge at 1440 x 900 with the app idle: the smooth
 * `ease-in-out` drift cost 11-15% of a CPU core and ~1.5% GPU (a new frame
 * every 1/60 s, re-blurring every glass surface above it); `steps(80)` per
 * 40 s leg, same path, cost ~2% and ~0%. The blooms move about a pixel a
 * second, so two updates a second look the same. This pin keeps a smooth
 * timing function from coming back.
 */

const css = readFileSync("app/globals.css", "utf8").replace(/\r\n/g, "\n");

/** Every declaration block whose selector list targets body::before. */
function bodyBeforeBlocks(): { selector: string; body: string }[] {
  const out: { selector: string; body: string }[] = [];
  const re = /([^{}]*body::before[^{}]*)\{([^}]*)\}/g;
  for (let m = re.exec(css); m; m = re.exec(css)) out.push({ selector: m[1].trim(), body: m[2] });
  return out;
}

function baseBlock(): string {
  const base = bodyBeforeBlocks().find((b) => b.selector.replace(/\/\*[\s\S]*?\*\//g, "").trim() === "body::before");
  if (!base) throw new Error("no plain body::before rule in globals.css");
  return base.body;
}

describe("the liquid layer drifts in steps (v1.410.1)", () => {
  it("animates liquid-drift with a steps() timing function, two-ish updates a second", () => {
    const m = baseBlock().match(/animation:\s*liquid-drift\s+(\d+(?:\.\d+)?)s\s+steps\((\d+)\)\s+infinite\s+alternate\s*;/);
    expect(m, "body::before must animate `liquid-drift <D>s steps(<N>) infinite alternate`").not.toBeNull();
    const duration = Number(m![1]);
    const steps = Number(m![2]);
    const keyframes = css.match(/@keyframes liquid-drift \{([\s\S]*?)\n\}/);
    expect(keyframes).not.toBeNull();
    const stops = (keyframes![1].match(/^\s*\d+%\s*\{/gm) ?? []).length;
    expect(stops).toBeGreaterThanOrEqual(2);
    // steps() applies per keyframe leg: (stops - 1) legs in one pass of <D>s.
    const perSecond = (steps * (stops - 1)) / duration;
    expect(perSecond).toBeGreaterThanOrEqual(1);
    expect(perSecond).toBeLessThanOrEqual(4);
  });

  it("no rule hands body::before a smooth timing function", () => {
    for (const b of bodyBeforeBlocks()) {
      expect(b.body, b.selector).not.toMatch(/animation-timing-function/);
      const anim = b.body.match(/animation:\s*([^;]+);/);
      if (anim && anim[1].trim() !== "none") {
        expect(anim[1], b.selector).toMatch(/steps\(/);
        expect(anim[1], b.selector).not.toMatch(/\b(ease|ease-in|ease-out|ease-in-out|linear|cubic-bezier)\b/);
      }
    }
  });

  it("keeps the look: same blur and blooms, still stopped by reduced motion and a background", () => {
    const base = baseBlock();
    expect(base).toMatch(/filter:\s*blur\(48px\) saturate\(125%\);/);
    expect((base.match(/radial-gradient\(/g) ?? []).length).toBe(3);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*body::before \{\s*animation: none;/);
    expect(css).toMatch(/:root\[data-background="on"\] body::before \{[^}]*display: none;/);
  });
});
