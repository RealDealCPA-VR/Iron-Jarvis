/**
 * v1.410.0 — an animated background behind the app keeps every word readable.
 *
 * The background is drawn at BACKGROUND_PEAK opacity with a SCRIM_ALPHA scrim
 * of the page colour over it, and a background draws only mixes of the main
 * colour, the second colour and the page colour (backgrounds/README.md). This
 * file measures text over that with ITS OWN WCAG helpers — never through the
 * module's `readabilityProblems`, which would be the generator grading its own
 * homework — over every hue x background feel x second colour x version.
 */
import { afterEach, describe, expect, it } from "vitest";

import { fromOklch } from "@/lib/color";
import {
  BACKGROUND_PEAK,
  SCRIM_ALPHA,
  generatePalette,
  loadPalettes,
  makePalette,
  normalizeBackground,
  normalizePalette,
  PALETTES_KEY,
  readabilityProblems,
  type Surface,
  type ThemeVars,
} from "@/lib/themePalette";
import { PRE_PAINT_SCRIPT } from "@/lib/theme";

type RGB = [number, number, number];
function lum([r, g, b]: RGB): number {
  const f = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function ratio(a: RGB, b: RGB): number {
  const x = lum(a);
  const y = lum(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
const mix = (fg: RGB, a: number, bg: RGB): RGB => [0, 1, 2].map((i) => fg[i] * a + bg[i] * (1 - a)) as RGB;
const rgb = (vars: ThemeVars, k: string): RGB => vars[k].split(" ").map(Number) as RGB;
const hex = (c: RGB) => `#${c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("")}`;

/** Everything text can sit on over a background — sampled finely. */
function under(vars: ThemeVars, scheme: "dark" | "light"): RGB[] {
  const g = rgb(vars, "ink-950");
  const a = rgb(vars, "accent-rgb");
  const d = rgb(vars, "accent-deep-rgb");
  const out: RGB[] = [];
  for (let t = 0; t <= 1.0001; t += 0.1) {
    const c = mix(a, t, d);
    for (let w = 0; w <= 1.0001; w += 0.1) {
      const pixel = mix(c, w, g);
      out.push(mix(g, SCRIM_ALPHA[scheme], mix(pixel, BACKGROUND_PEAK, g)));
    }
  }
  return out;
}

const INKS = ["accent-rgb", "accent-soft-rgb", "zinc-300", "zinc-400", "zinc-500", "zinc-600"];
const TONES = ["success", "danger", "warn", "info", "violet"].map((t) => `tone-${t}`);

function failures(vars: ThemeVars, scheme: "dark" | "light"): string[] {
  const out: string[] = [];
  const S = under(vars, scheme);
  for (const k of [...INKS, ...TONES]) {
    const worst = Math.min(...S.map((s) => ratio(rgb(vars, k), s)));
    if (worst < 4.5) out.push(`${k} ${worst.toFixed(2)}`);
  }
  const card = rgb(vars, "ink-850");
  const c = (k: string) => ratio(rgb(vars, k), card);
  if (!(c("zinc-400") > c("zinc-500") && c("zinc-500") > c("zinc-600"))) out.push("zinc order");
  return out;
}

const PICKS: RGB[] = [
  ...Array.from({ length: 36 }, (_, i) => fromOklch({ l: 0.7, c: 0.15, h: i * 10 })),
  [255, 255, 255],
  [0, 0, 0],
  [119, 119, 119],
  [255, 255, 0],
  [0, 0, 255],
  [185, 28, 28],
  [253, 224, 71],
  [30, 41, 59],
];
const SURFACES: Surface[] = ["neutral", "cool", "warm", "tinted"];

afterEach(() => localStorage.clear());

describe("A. text over scrim plus background reads in both versions", () => {
  it.each(SURFACES)("background feel %s", (surface) => {
    const bad: string[] = [];
    for (const pick of PICKS)
      for (const second of [null, "#b91c1c", "#fde047"]) {
        const g = generatePalette({ accent: hex(pick), second, surface, withBackground: true });
        for (const scheme of ["dark", "light"] as const)
          for (const f of failures(g.vars[scheme], scheme)) bad.push(`${hex(pick)}/${second} ${scheme}: ${f}`);
      }
    expect(bad).toEqual([]);
  });

  it("the maker's own check agrees: no problem reported for the sweep", () => {
    for (const pick of PICKS.slice(0, 36)) {
      const g = generatePalette({ accent: hex(pick), surface: "cool", withBackground: true });
      expect(readabilityProblems(g.vars.dark, "dark", true)).toEqual([]);
      expect(readabilityProblems(g.vars.light, "light", true)).toEqual([]);
    }
  });

  it("the check measures the background: a palette made WITHOUT one can fail over one", () => {
    // Arc Cyan's plain palette has its hint text at ~4.6:1 on the page —
    // over a cyan background it cannot hold, and the check must say so.
    const plain = generatePalette({ accent: "#22d3ee", surface: "cool" });
    expect(readabilityProblems(plain.vars.dark, "dark", false)).toEqual([]);
    expect(readabilityProblems(plain.vars.dark, "dark", true).join(" ")).toMatch(/over the background/);
    expect(failures(plain.vars.dark, "dark").length).toBeGreaterThan(0);
  });

  it("a palette without a background is byte-identical to before", () => {
    const a = generatePalette({ accent: "#6366f1", second: "#ec4899", surface: "warm" });
    const b = generatePalette({ accent: "#6366f1", second: "#ec4899", surface: "warm", withBackground: false });
    expect(a).toEqual(b);
  });
});

describe("B. the choice is saved with the palette", () => {
  it("makePalette keeps the background and its motion; an unknown id is dropped", () => {
    const p = makePalette({
      name: "Bg",
      accent: "#22d3ee",
      surface: "cool",
      mode: "dark",
      background: { id: "aurora", motion: "still" },
    });
    expect(p.background).toEqual({ id: "aurora", motion: "still" });
    expect(normalizeBackground({ id: "not-a-background", motion: "animated" })).toBeNull();
    expect(normalizeBackground({ id: "waves", motion: "sideways" })).toEqual({ id: "waves", motion: "animated" });
    expect(normalizeBackground(null)).toBeNull();
  });

  it("a stored palette round-trips through ij_palettes with its background", () => {
    const p = makePalette({ name: "X", accent: "#a855f7", surface: "tinted", mode: "system", background: { id: "reactor", motion: "animated" } });
    localStorage.setItem(PALETTES_KEY, JSON.stringify([p]));
    expect(loadPalettes()[0].background).toEqual({ id: "reactor", motion: "animated" });
    // An older entry (no background key) loads with none.
    const { background: _drop, ...old } = p;
    void _drop;
    expect(normalizePalette(old)?.background).toBeNull();
  });

  it("the pre-paint script still paints a palette whose background file is gone", () => {
    const p = makePalette({ name: "Gone", accent: "#ef4444", surface: "neutral", mode: "dark", background: { id: "waves", motion: "animated" } });
    localStorage.setItem(PALETTES_KEY, JSON.stringify([p]));
    localStorage.setItem("ij_theme", p.id);
    const html = document.documentElement;
    // eslint-disable-next-line no-new-func
    new Function(PRE_PAINT_SCRIPT)();
    expect(html.dataset.theme).toBe(p.id);
    expect(html.style.getPropertyValue("--accent-rgb")).toBe(p.vars.dark["accent-rgb"]);
    delete html.dataset.theme;
    delete html.dataset.scheme;
    html.removeAttribute("style");
  });
});
