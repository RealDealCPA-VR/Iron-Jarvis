/**
 * YOUR OWN THEME (v1.317.0): a palette from a few picks, in a dark AND a light
 * version, readable by construction.
 *
 * The app paints entirely from ~34 CSS variables (globals.css → tailwind.config:
 * accent, ink surfaces, the zinc text ramp, `--white`, the tone inks). The
 * built-in Marks set them in stylesheet blocks; a custom palette sets the SAME
 * variables inline on <html> (lib/theme.ts `applyTheme`, and the layout's
 * pre-paint script before the first frame). Nothing else in the app has to
 * know a palette is custom.
 *
 * What the person picks: a main colour, optionally a second colour (it only
 * tints background glows and gradients — `accent-deep`, never text), a
 * background feel (neutral / cool / warm / tinted with the main colour) and
 * whether to show the dark version, the light one, or follow Windows.
 *
 * How both versions stay readable: the surfaces and text ramps start from the
 * two vetted built-in palettes (Arc Cyan for dark, Daylight for light — the
 * BASE_* tables below, pinned equal to globals.css by test) and keep their
 * LIGHTNESS while taking the chosen hue, so contrast barely moves. Then every
 * token text is drawn in is checked with the same WCAG formula the theme tests
 * use, and nudged lighter (dark version) or deeper (light version) until it
 * reaches 4.5:1 — a pale yellow main colour becomes a deeper gold in the light
 * version instead of white-on-white links. `notes` says, in words, what moved.
 *
 * Storage is per device (localStorage, like the theme choice itself); the
 * computed variables are stored WITH the palette so the pre-paint script can
 * apply them without running this file. `PALETTE_VERSION` re-computes stored
 * palettes when this generator changes.
 */

import {
  contrast,
  fitLightness,
  fromOklch,
  fromTriplet,
  over,
  parseHex,
  toHex,
  toOklch,
  toTriplet,
  type RGB,
} from "./color";

export const PALETTE_VERSION = 1;
export const PALETTES_KEY = "ij_palettes";
/** Fired on window whenever the saved list changes (this tab). */
export const PALETTES_EVENT = "ij-palettes-change";
export const MAX_PALETTES = 12;
export const MAX_NAME = 40;

export type Surface = "neutral" | "cool" | "warm" | "tinted";
export type PaletteMode = "dark" | "light" | "system";
export type Scheme = "dark" | "light";
/** CSS variable name (without `--`) → `"r g b"`. */
export type ThemeVars = Record<string, string>;

export interface PaletteSpec {
  accent: string;
  second?: string | null;
  surface: Surface;
}

export interface CustomPalette extends PaletteSpec {
  id: string;
  name: string;
  second: string | null;
  mode: PaletteMode;
  v: number;
  vars: Record<Scheme, ThemeVars>;
}

export const SURFACES: { id: Surface; label: string; hint: string }[] = [
  { id: "neutral", label: "Neutral", hint: "Plain greys" },
  { id: "cool", label: "Cool", hint: "A hint of blue" },
  { id: "warm", label: "Warm", hint: "A hint of sand" },
  { id: "tinted", label: "Tinted", hint: "A hint of your main colour" },
];

export const MODES: { id: PaletteMode; label: string }[] = [
  { id: "dark", label: "Dark" },
  { id: "light", label: "Light" },
  { id: "system", label: "Match Windows" },
];

/** Starting points: the built-in Marks, as picks. */
export const PRESETS: { from: string; name: string; spec: PaletteSpec }[] = [
  { from: "mark2", name: "Arc Cyan", spec: { accent: "#22d3ee", second: null, surface: "cool" } },
  { from: "mark1", name: "Daylight", spec: { accent: "#0891b2", second: null, surface: "cool" } },
  { from: "mark8", name: "Liquid Glass", spec: { accent: "#0a84ff", second: null, surface: "cool" } },
  { from: "mark23", name: "Gold & Red", spec: { accent: "#f5b731", second: "#b91c1c", surface: "warm" } },
  { from: "mark29", name: "Silver & Red", spec: { accent: "#bfc8d6", second: "#dc2626", surface: "neutral" } },
];

/** A few quick picks beside the colour well. */
export const SWATCHES = [
  "#22d3ee",
  "#0a84ff",
  "#6366f1",
  "#a855f7",
  "#ec4899",
  "#ef4444",
  "#f97316",
  "#f5b731",
  "#84cc16",
  "#10b981",
  "#14b8a6",
  "#94a3b8",
];

/* ------------------------------------------------------------- bases */

/** The dark default (`:root` in globals.css) — pinned equal by test. */
export const BASE_DARK: ThemeVars = {
  "accent-rgb": "34 211 238",
  "accent-soft-rgb": "103 232 249",
  "accent-deep-rgb": "8 145 178",
  "accent-dim-rgb": "14 116 144",
  "ink-950": "7 8 9",
  "ink-900": "11 13 17",
  "ink-875": "15 18 23",
  "ink-850": "19 22 29",
  "ink-800": "24 28 37",
  "ink-750": "31 36 48",
  "ink-700": "39 45 59",
  "ink-600": "52 60 78",
  white: "255 255 255",
  "zinc-50": "250 250 250",
  "zinc-100": "244 244 245",
  "zinc-200": "228 228 231",
  "zinc-300": "212 212 216",
  "zinc-400": "161 161 170",
  "zinc-500": "145 153 168",
  "zinc-600": "118 125 140",
  "zinc-700": "63 63 70",
  "zinc-800": "39 39 42",
  "zinc-900": "24 24 27",
  "zinc-950": "9 9 11",
  "tone-success": "110 231 183",
  "tone-danger": "253 164 175",
  "tone-warn": "252 211 77",
  "tone-info": "125 211 252",
  "tone-violet": "196 181 253",
};

/** Daylight (`:root[data-theme="mark1"]`) — pinned equal by test. */
export const BASE_LIGHT: ThemeVars = {
  "accent-rgb": "11 100 125",
  "accent-soft-rgb": "14 116 144",
  "accent-deep-rgb": "21 94 117",
  "accent-dim-rgb": "22 78 99",
  "ink-950": "237 240 245",
  "ink-900": "244 246 250",
  "ink-875": "240 243 248",
  "ink-850": "252 253 255",
  "ink-800": "255 255 255",
  "ink-750": "230 233 239",
  "ink-700": "222 226 233",
  "ink-600": "208 213 221",
  white: "22 26 33",
  "zinc-50": "24 27 33",
  "zinc-100": "31 35 43",
  "zinc-200": "45 51 62",
  "zinc-300": "51 58 71",
  "zinc-400": "70 77 91",
  "zinc-500": "84 92 107",
  "zinc-600": "96 104 119",
  "zinc-700": "176 182 194",
  "zinc-800": "205 210 219",
  "zinc-900": "224 228 235",
  "zinc-950": "240 242 246",
  "tone-success": "6 95 70",
  "tone-danger": "159 18 57",
  "tone-warn": "146 64 14",
  "tone-info": "7 89 133",
  "tone-violet": "91 33 182",
};

/** Every variable a palette sets — and so every one a switch back clears. */
export const THEME_VAR_KEYS: string[] = Object.keys(BASE_DARK);

const TONES = ["success", "danger", "warn", "info", "violet"] as const;
/** The three surfaces text sits on: page, recessed panel, card. */
const TEXT_SURFACES = ["ink-950", "ink-900", "ink-850"] as const;
const AA = 4.5;
/** A hair above AA, so rounding to whole RGB values can never land below it. */
const AIM = 4.6;
const TONE_TINT = 0.12;

const SURFACE_HUE: Record<"cool" | "warm", number> = { cool: 255, warm: 62 };

function rgbOf(vars: ThemeVars, key: string): RGB {
  return fromTriplet(vars[key]) ?? [128, 128, 128];
}

/** Keep a base colour's lightness; give it the chosen background feel. */
function feel(base: RGB, surface: Surface, accentHue: number, scale: number): RGB {
  const o = toOklch(base);
  if (surface === "neutral") return fromOklch({ ...o, c: 0 });
  if (surface === "tinted")
    return fromOklch({ l: o.l, c: Math.min(0.045, (o.c + 0.012) * 1.7) * scale, h: accentHue });
  return fromOklch({ l: o.l, c: Math.max(o.c, 0.012) * scale, h: SURFACE_HUE[surface] });
}

const withL = (rgb: RGB, dl: number, mul = 1): RGB => {
  const o = toOklch(rgb);
  return fromOklch({ ...o, l: Math.min(0.97, Math.max(0.12, o.l * mul + dl)) });
};

function buildScheme(
  scheme: Scheme,
  pick: RGB,
  second: RGB | null,
  surface: Surface,
): { vars: ThemeVars; moved: boolean } {
  const base = scheme === "dark" ? BASE_DARK : BASE_LIGHT;
  const dir: 1 | -1 = scheme === "dark" ? 1 : -1;
  const hue = toOklch(pick).h;
  const out: Record<string, RGB> = {};

  for (const k of THEME_VAR_KEYS) {
    const v = rgbOf(base, k);
    if (k.startsWith("ink-")) out[k] = feel(v, surface, hue, 1);
    else if (k.startsWith("zinc-") || k === "white") out[k] = feel(v, surface, hue, 0.6);
    else out[k] = v; // tones (and accents, replaced below)
  }
  const S = TEXT_SURFACES.map((k) => out[k]);

  // Accent: links, active tabs, and the primary button's fill — whose label is
  // drawn in ink-950, which is one of S, so one condition covers both.
  const accent = fitLightness(pick, S, AIM, dir);
  out["accent-rgb"] = accent;
  out["accent-soft-rgb"] = fitLightness(withL(accent, scheme === "dark" ? 0.08 : 0.05), S, AIM, dir);
  // Decoration only (background glows, a gradient end): never text.
  out["accent-deep-rgb"] = second ?? withL(accent, scheme === "dark" ? 0 : -0.05, scheme === "dark" ? 0.74 : 1);
  out["accent-dim-rgb"] = withL(accent, scheme === "dark" ? 0 : -0.08, scheme === "dark" ? 0.62 : 1);

  // The text ramp: helper text (500) and hints (600) must read on every
  // surface. (The ramp ORDER and the status inks keep the vetted bases'
  // margins under every background feel — measured over 36 hues x 4 feels x
  // both versions in theme-maker-v1317 — so they need no adjusting here; that
  // sweep is what fails if a later change to the bases or to `feel` breaks
  // either.)
  out["zinc-600"] = fitLightness(out["zinc-600"], S, AIM, dir);
  out["zinc-500"] = fitLightness(out["zinc-500"], S, AIM, dir);

  const vars: ThemeVars = {};
  for (const k of THEME_VAR_KEYS) vars[k] = toTriplet(out[k]);
  const moved = toHex(accent) !== toHex(pick);
  return { vars, moved };
}

export interface GeneratedPalette {
  vars: Record<Scheme, ThemeVars>;
  /** Plain sentences about anything the maker changed to keep text readable. */
  notes: string[];
}

export function generatePalette(spec: PaletteSpec): GeneratedPalette {
  const pick = parseHex(spec.accent) ?? [34, 211, 238];
  const second = spec.second ? parseHex(spec.second) : null;
  // "Tinted" with a grey main colour has no hue to tint with.
  const surface: Surface =
    spec.surface === "tinted" && toOklch(pick).c < 0.02 ? "neutral" : spec.surface;
  const dark = buildScheme("dark", pick, second, surface);
  const light = buildScheme("light", pick, second, surface);
  const notes: string[] = [];
  if (dark.moved)
    notes.push("In the dark version your main colour is a little brighter, so text in it stays easy to read.");
  if (light.moved)
    notes.push("In the light version your main colour is a little deeper, so text in it stays easy to read.");
  return { vars: { dark: dark.vars, light: light.vars }, notes };
}

/**
 * The same readability checks the theme tests apply to the built-in palettes,
 * as sentences. Empty = every check passes.
 */
export function readabilityProblems(vars: ThemeVars, scheme: Scheme): string[] {
  const c = (a: string, b: string) => contrast(rgbOf(vars, a), rgbOf(vars, b));
  const out: string[] = [];
  for (const s of TEXT_SURFACES) {
    if (c("accent-rgb", s) < AA) out.push(`main colour on ${s}`);
    if (c("accent-soft-rgb", s) < AA) out.push(`link colour on ${s}`);
    if (c("zinc-600", s) < AA) out.push(`hint text on ${s}`);
    if (c("zinc-500", s) < AA) out.push(`helper text on ${s}`);
    for (const t of TONES) if (c(`tone-${t}`, s) < AA) out.push(`${t} colour on ${s}`);
  }
  if (c("ink-950", "accent-rgb") < AA) out.push("button label on the main colour");
  if (c("ink-950", "accent-soft-rgb") < AA) out.push("button label on its hover colour");
  for (const t of TONES) {
    const ink = rgbOf(vars, `tone-${t}`);
    if (contrast(ink, over(ink, TONE_TINT, rgbOf(vars, "ink-950"))) < AA) out.push(`${t} badge`);
  }
  const card = rgbOf(vars, "ink-850");
  const z = (k: string) => contrast(rgbOf(vars, k), card);
  if (!(z("zinc-400") > z("zinc-500") && z("zinc-500") > z("zinc-600"))) out.push("text ramp order");
  void scheme;
  return out;
}

/** The colour a palette's dot is drawn in for each bar (dark / light). */
export function accentHex(p: Pick<CustomPalette, "vars">, scheme: Scheme): string {
  return toHex(rgbOf(p.vars[scheme], "accent-rgb"));
}

export function schemeFor(mode: PaletteMode, prefersLight: boolean): Scheme {
  if (mode === "system") return prefersLight ? "light" : "dark";
  return mode;
}

/* ----------------------------------------------------------- storage */

const ID = /^custom-[a-z0-9]{1,24}$/;
const SURFACE_IDS = new Set(SURFACES.map((s) => s.id));
const MODE_IDS = new Set(MODES.map((m) => m.id));

export function newPaletteId(): string {
  return `custom-${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`;
}

function varsOk(v: unknown): v is ThemeVars {
  if (!v || typeof v !== "object") return false;
  const rec = v as Record<string, unknown>;
  return THEME_VAR_KEYS.every((k) => typeof rec[k] === "string" && fromTriplet(rec[k] as string) !== null);
}

/** A stored entry, checked; null when it cannot be a palette. */
export function normalizePalette(raw: unknown): CustomPalette | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== "string" || !ID.test(r.id)) return null;
  if (typeof r.accent !== "string" || !parseHex(r.accent)) return null;
  const second = typeof r.second === "string" && parseHex(r.second) ? toHex(parseHex(r.second)!) : null;
  const surface = SURFACE_IDS.has(r.surface as Surface) ? (r.surface as Surface) : "cool";
  const mode = MODE_IDS.has(r.mode as PaletteMode) ? (r.mode as PaletteMode) : "dark";
  const name =
    (typeof r.name === "string" ? r.name.trim().slice(0, MAX_NAME) : "") || "My theme";
  const accent = toHex(parseHex(r.accent)!);
  const vars = r.vars as Record<string, unknown> | undefined;
  const fresh =
    r.v === PALETTE_VERSION && vars && varsOk(vars.dark) && varsOk(vars.light)
      ? { dark: vars.dark as ThemeVars, light: vars.light as ThemeVars }
      : generatePalette({ accent, second, surface }).vars;
  return { id: r.id, name, accent, second, surface, mode, v: PALETTE_VERSION, vars: fresh };
}

export function loadPalettes(): CustomPalette[] {
  try {
    const raw = JSON.parse(localStorage.getItem(PALETTES_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    const seen = new Set<string>();
    const out: CustomPalette[] = [];
    for (const r of raw) {
      const p = normalizePalette(r);
      if (p && !seen.has(p.id)) {
        seen.add(p.id);
        out.push(p);
      }
    }
    return out.slice(0, MAX_PALETTES);
  } catch {
    return [];
  }
}

export function savePalettes(list: CustomPalette[]): boolean {
  try {
    localStorage.setItem(PALETTES_KEY, JSON.stringify(list.slice(0, MAX_PALETTES)));
  } catch {
    return false;
  } finally {
    if (typeof window !== "undefined") window.dispatchEvent(new Event(PALETTES_EVENT));
  }
  return true;
}

/** Build a stored palette from picks (computes both versions). */
export function makePalette(
  input: PaletteSpec & { id?: string; name: string; mode: PaletteMode },
): CustomPalette {
  const accent = toHex(parseHex(input.accent) ?? [34, 211, 238]);
  const second = input.second && parseHex(input.second) ? toHex(parseHex(input.second)!) : null;
  return {
    id: input.id && ID.test(input.id) ? input.id : newPaletteId(),
    name: input.name.trim().slice(0, MAX_NAME) || "My theme",
    accent,
    second,
    surface: input.surface,
    mode: input.mode,
    v: PALETTE_VERSION,
    vars: generatePalette({ accent, second, surface: input.surface }).vars,
  };
}

/** Insert or replace by id; false when the list is full or storage failed. */
export function upsertPalette(p: CustomPalette): boolean {
  const list = loadPalettes();
  const i = list.findIndex((x) => x.id === p.id);
  if (i >= 0) list[i] = p;
  else if (list.length >= MAX_PALETTES) return false;
  else list.push(p);
  return savePalettes(list);
}

export function deletePalette(id: string): boolean {
  return savePalettes(loadPalettes().filter((p) => p.id !== id));
}
