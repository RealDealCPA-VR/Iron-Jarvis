/**
 * THE THEME STORE (v1.314.0, UX wave 2 — "theme everywhere").
 *
 * Wave 1 put the theme row in the title bar and the phone's nav drawer; this
 * wave adds a Settings → Appearance row and "Theme: <name>" commands in the
 * command palette. Four ways in, so the list and the setter live HERE, once:
 * a second copy of the Mark list in the palette is exactly how a renamed or
 * added theme would end up offered in one place and not the other.
 *
 * The store itself is the DOM + localStorage, as it has been since the
 * switcher shipped: `<html data-theme>` paints (globals.css), `ij_theme` is
 * what the layout's pre-paint script restores. Every ThemeSwitcher watches
 * the attribute (a MutationObserver), so whoever calls `applyTheme` — a bar
 * dot, the Settings row, a palette command — every other row follows.
 * A theme is a per-device choice: it never rides the daemon's Save.
 */

import {
  accentHex,
  loadPalettes,
  PALETTES_KEY,
  schemeFor,
  THEME_VAR_KEYS,
  type CustomPalette,
} from "./themePalette";

export interface Mark {
  id: string; // data-theme value
  mark: string; // "Mark 1"
  name: string;
  flavor: string;
  accent: string; // preview color (each reactor shows ITS theme's color)
  /** The same hue, deep enough to read on a LIGHT bar (>= 3:1 on the light
   *  Marks' --ink-950). Only used while a light Mark is on. */
  onLight: string;
}

export const THEMES: Mark[] = [
  {
    id: "mark1",
    mark: "Mark 1",
    name: "Daylight",
    flavor: "Dark work on a bright canvas — full light mode.",
    accent: "#0891b2",
    onLight: "#155e75",
  },
  {
    id: "mark2",
    mark: "Mark 2",
    name: "Arc Cyan",
    flavor: "The signature reactor glow. Balanced and cool.",
    accent: "#22d3ee",
    onLight: "#0e7490",
  },
  {
    id: "mark8",
    mark: "Mark 8",
    name: "Liquid Glass",
    flavor: "Frosted glass on silver light — clean, airy, unmistakably modern.",
    accent: "#0a84ff",
    onLight: "#0062cc",
  },
  {
    id: "mark23",
    mark: "Mark 23",
    name: "Gold & Red",
    flavor: "The classic hero colors — powered up.",
    accent: "#f5b731",
    onLight: "#92600a",
  },
  {
    id: "mark29",
    mark: "Mark 29",
    name: "Silver & Red",
    flavor: "Sleek chrome with a red-line edge.",
    accent: "#bfc8d6",
    onLight: "#5b6576",
  },
];

export const THEME_STORAGE_KEY = "ij_theme";
export const DEFAULT_THEME = "mark2";
/** The Marks that paint a light bar (globals.css). */
export const LIGHT_MARKS = new Set(["mark1", "mark8"]);

/* ---------------------------------------------------------------------------
 * v1.317.0 — YOUR OWN THEME, and light/dark as a SCHEME.
 *
 * `<html data-scheme="light|dark">` is the one place "is this a light theme"
 * is answered: globals.css keys every light re-ink on it (it used to list the
 * two light Mark ids, so a light palette of the user's own would have kept the
 * dark-mode pale reds and greens on white), and the canvas, the switcher's
 * dots and the palette commands read it. The built-in Marks get their scheme
 * from LIGHT_MARKS; a custom palette (lib/palette.ts) from its mode — "Match
 * Windows" follows the OS live.
 *
 * A custom palette sets the theme variables INLINE on <html> (the Marks set
 * them in stylesheet blocks), with `data-theme` = its own id so every observer
 * of the attribute (title bar overlay, switcher rows) notices the change.
 * ------------------------------------------------------------------------- */

export type Scheme = "dark" | "light";

/** The scheme the document is painted in right now. */
export function currentScheme(): Scheme {
  if (typeof document === "undefined") return "dark";
  const html = document.documentElement;
  const theme = html.dataset.theme ?? "";
  // A Mark's scheme is fixed by the Mark; only a palette's is chosen.
  if (!isCustomTheme(theme)) return LIGHT_MARKS.has(theme) ? "light" : "dark";
  return html.dataset.scheme === "light" ? "light" : "dark";
}

export function isCustomTheme(id: string): boolean {
  return id.startsWith("custom-");
}

/** A saved palette as a row in the theme lists (dots, drawer, palette). */
export function paletteMark(p: CustomPalette): Mark {
  const flavor =
    p.mode === "system"
      ? "Your theme — follows Windows' light or dark setting."
      : p.mode === "light"
        ? "Your theme — light version."
        : "Your theme — dark version.";
  return {
    id: p.id,
    mark: "Your theme",
    name: p.name,
    flavor,
    accent: accentHex(p, "dark"),
    onLight: accentHex(p, "light"),
  };
}

/** Built-in Marks first, then this device's own palettes. */
export function allThemes(): Mark[] {
  return [...THEMES, ...loadPalettes().map(paletteMark)];
}

/** Is `id` painted light right now (a Mark by its list, a palette by its mode)? */
export function isLightTheme(id: string): boolean {
  if (!isCustomTheme(id)) return LIGHT_MARKS.has(id);
  const p = loadPalettes().find((x) => x.id === id);
  return !!p && schemeFor(p.mode, prefersLight()) === "light";
}

function prefersLight(): boolean {
  try {
    return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-color-scheme: light)").matches;
  } catch {
    return false;
  }
}

function clearInlineVars(html: HTMLElement) {
  for (const k of THEME_VAR_KEYS) html.style.removeProperty(`--${k}`);
  html.style.removeProperty("color-scheme");
}

/* "Match Windows": one listener, armed only while such a palette is on. */
let systemQuery: MediaQueryList | null = null;
const onSystemChange = () => {
  const id = document.documentElement.dataset.theme ?? "";
  if (isCustomTheme(id)) applyTheme(id);
};
function followSystem(on: boolean) {
  if (typeof window === "undefined" || !window.matchMedia) return;
  if (on && !systemQuery) {
    systemQuery = window.matchMedia("(prefers-color-scheme: light)");
    systemQuery.addEventListener?.("change", onSystemChange);
  } else if (!on && systemQuery) {
    systemQuery.removeEventListener?.("change", onSystemChange);
    systemQuery = null;
  }
}

let transitionTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * Switch the whole app to theme `id` — a built-in Mark or one of this
 * device's palettes: flip `<html data-theme>` and `data-scheme` (with the
 * brief colour morph) and remember it on this device. Returns the theme's
 * row, or null for an id that is not a theme (nothing changes then).
 */
export function applyTheme(id: string): Mark | null {
  const palette = isCustomTheme(id) ? (loadPalettes().find((p) => p.id === id) ?? null) : null;
  const mark = palette ? paletteMark(palette) : (THEMES.find((t) => t.id === id) ?? null);
  if (!mark || typeof document === "undefined") return mark;
  const html = document.documentElement;
  html.classList.add("theme-transition"); // smooth color morph, briefly
  if (palette) {
    const scheme = schemeFor(palette.mode, prefersLight());
    // Variables first, attributes last: an observer of data-theme reads the
    // computed palette in its callback, which must already be the new one.
    for (const [k, v] of Object.entries(palette.vars[scheme])) html.style.setProperty(`--${k}`, v);
    html.style.setProperty("color-scheme", scheme);
    html.dataset.scheme = scheme;
    html.dataset.theme = palette.id;
    followSystem(palette.mode === "system");
  } else {
    clearInlineVars(html);
    html.dataset.scheme = LIGHT_MARKS.has(mark.id) ? "light" : "dark";
    html.dataset.theme = mark.id;
    followSystem(false);
  }
  try {
    localStorage.setItem(THEME_STORAGE_KEY, mark.id);
  } catch {
    /* private mode — the theme still applies for this session */
  }
  if (transitionTimer) clearTimeout(transitionTimer);
  transitionTimer = setTimeout(() => html.classList.remove("theme-transition"), 520);
  return mark;
}

/**
 * The layout's pre-paint script: restores the theme, its scheme and — for a
 * palette — its variables BEFORE the first frame, so there is no flash of Arc
 * Cyan. ES5, self-contained, never throws. A palette that is gone falls back
 * to the default (no data-theme), and a stored variable is applied only when
 * it is a known name with an `r g b` value.
 */
export const PRE_PAINT_SCRIPT = [
  "try{",
  "var d=document.documentElement,t=localStorage.getItem(" + JSON.stringify(THEME_STORAGE_KEY) + "),s='dark';",
  "var LM=" + JSON.stringify([...LIGHT_MARKS]) + ",K=" + JSON.stringify(THEME_VAR_KEYS) + ";",
  "if(t&&t.indexOf('custom-')===0){",
  "var L=JSON.parse(localStorage.getItem(" + JSON.stringify(PALETTES_KEY) + ")||'[]'),p=null;",
  "for(var i=0;i<L.length;i++)if(L[i]&&L[i].id===t)p=L[i];",
  "if(p&&p.vars){",
  "s=p.mode==='light'?'light':p.mode==='system'&&window.matchMedia&&matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';",
  "var v=p.vars[s]||{};",
  "for(var j=0;j<K.length;j++){var x=v[K[j]];if(typeof x==='string'&&/^\\d{1,3} \\d{1,3} \\d{1,3}$/.test(x))d.style.setProperty('--'+K[j],x)}",
  "d.style.setProperty('color-scheme',s);d.dataset.theme=t",
  "}",
  "}else if(t){d.dataset.theme=t;if(LM.indexOf(t)>=0)s='light'}",
  "d.dataset.scheme=s",
  "}catch(e){}",
].join("");
