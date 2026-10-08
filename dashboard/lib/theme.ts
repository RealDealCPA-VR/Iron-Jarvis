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

let transitionTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * Switch the whole app to theme `id`: flip `<html data-theme>` (with the
 * brief colour morph) and remember it on this device. Returns the Mark, or
 * null for an id that is not a theme (nothing changes then).
 */
export function applyTheme(id: string): Mark | null {
  const mark = THEMES.find((t) => t.id === id) ?? null;
  if (!mark || typeof document === "undefined") return mark;
  const html = document.documentElement;
  html.classList.add("theme-transition"); // smooth color morph, briefly
  html.dataset.theme = mark.id;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, mark.id);
  } catch {
    /* private mode — the theme still applies for this session */
  }
  if (transitionTimer) clearTimeout(transitionTimer);
  transitionTimer = setTimeout(() => html.classList.remove("theme-transition"), 520);
  return mark;
}
