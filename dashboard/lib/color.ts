/**
 * Colour maths for the theme maker (v1.317.0).
 *
 * Two jobs, both pure and offline:
 *  - WCAG 2 contrast (`luminance`, `contrast`, `over`) — the SAME formula the
 *    theme tests use (the 0.03928 threshold), so a palette the maker calls
 *    readable is one the tests call readable.
 *  - OKLCH, a perceptual colour space, so "make this colour lighter until it
 *    reads" keeps its hue instead of drifting toward grey or another colour.
 *    (Björn Ottosson's OKLab, public domain.)
 *
 * Colours travel as `[r, g, b]` 0-255 triplets; the CSS variables hold them as
 * space-separated `"r g b"` strings (`rgb(var(--x) / a)` in tailwind.config).
 */

export type RGB = [number, number, number];
/** L 0-1, C (chroma) 0-~0.37, H degrees. */
export interface Oklch {
  l: number;
  c: number;
  h: number;
}

const HEX = /^#?([0-9a-f]{6})$/i;

/** `#22d3ee` / `22D3EE` → [34, 211, 238]; anything else → null. */
export function parseHex(hex: string): RGB | null {
  const m = HEX.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function toHex([r, g, b]: RGB): string {
  return `#${[r, g, b].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("")}`;
}

/** The CSS variable form: `"34 211 238"`. */
export function toTriplet([r, g, b]: RGB): string {
  return `${Math.round(r)} ${Math.round(g)} ${Math.round(b)}`;
}

export function fromTriplet(s: string): RGB | null {
  const m = /^\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s*$/.exec(s);
  if (!m) return null;
  const v = [Number(m[1]), Number(m[2]), Number(m[3])];
  return v.every((x) => x <= 255) ? (v as RGB) : null;
}

/* ------------------------------------------------------------- contrast */

export function luminance([r, g, b]: RGB): number {
  const f = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

export function contrast(a: RGB, b: RGB): number {
  const x = luminance(a);
  const y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

/** `fg` at `alpha` painted over an opaque `bg`. */
export function over(fg: RGB, alpha: number, bg: RGB): RGB {
  return [0, 1, 2].map((i) => fg[i] * alpha + bg[i] * (1 - alpha)) as RGB;
}

/* ---------------------------------------------------------------- OKLCH */

const toLinear = (v: number) => {
  const s = v / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
const fromLinear = (v: number) => {
  const s = v <= 0.0031308 ? v * 12.92 : 1.055 * v ** (1 / 2.4) - 0.055;
  return s * 255;
};

export function toOklch(rgb: RGB): Oklch {
  const [r, g, b] = rgb.map(toLinear);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  const c = Math.sqrt(A * A + B * B);
  let h = (Math.atan2(B, A) * 180) / Math.PI;
  if (h < 0) h += 360;
  return { l: L, c, h };
}

/** Linear sRGB for an OKLCH colour — may be out of gamut (outside 0..1). */
function linearOf({ l, c, h }: Oklch): [number, number, number] {
  const hr = (h * Math.PI) / 180;
  const A = c * Math.cos(hr);
  const B = c * Math.sin(hr);
  const l_ = (l + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m_ = (l - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s_ = (l - 0.0894841775 * A - 1.291485548 * B) ** 3;
  return [
    4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
    -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
    -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
  ];
}

const inGamut = (v: number[]) => v.every((x) => x >= -1e-4 && x <= 1 + 1e-4);

/**
 * OKLCH → sRGB, keeping lightness and hue and giving up CHROMA when the colour
 * cannot be shown on a screen (a very light, very saturated blue does not
 * exist; the nearest real one is a paler blue, not a purple).
 */
export function fromOklch(o: Oklch): RGB {
  const l = Math.min(1, Math.max(0, o.l));
  let c = Math.max(0, o.c);
  if (!inGamut(linearOf({ l, c, h: o.h }))) {
    let lo = 0;
    let hi = c;
    for (let i = 0; i < 24; i++) {
      const mid = (lo + hi) / 2;
      if (inGamut(linearOf({ l, c: mid, h: o.h }))) lo = mid;
      else hi = mid;
    }
    c = lo;
  }
  return linearOf({ l, c, h: o.h }).map((v) =>
    Math.min(255, Math.max(0, Math.round(fromLinear(Math.min(1, Math.max(0, v)))))),
  ) as RGB;
}

/**
 * The smallest lightness move (up when `dir` is 1, down when -1) that makes
 * `colour` reach `min` contrast against EVERY colour in `against`. Hue is kept;
 * chroma only yields to the screen's gamut. Returns the colour unchanged when
 * it already reads, and the end of the range (white / black side) when even
 * that falls short — the caller's tests then say so.
 */
export function fitLightness(colour: RGB, against: RGB[], min: number, dir: 1 | -1): RGB {
  const ok = (rgb: RGB) => against.every((bg) => contrast(rgb, bg) >= min);
  if (ok(colour)) return colour;
  const base = toOklch(colour);
  let pass = dir > 0 ? 1 : 0; // the far end: the most contrast this hue can have
  let fail = base.l;
  for (let i = 0; i < 28; i++) {
    const mid = (pass + fail) / 2;
    if (ok(fromOklch({ ...base, l: mid }))) pass = mid;
    else fail = mid;
  }
  return fromOklch({ ...base, l: pass });
}
