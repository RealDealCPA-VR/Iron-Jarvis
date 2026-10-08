/**
 * v1.317.0 — make your own theme: a dark AND a light version from a few
 * picks, readable by construction, applied everywhere the Marks are.
 *
 * Contrast is measured HERE with this file's own WCAG helpers (the same
 * formula as ux-wave1-design-v1313), never through the module's own
 * `readabilityProblems` — a generator that grades its own homework would
 * pass a broken check.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fromOklch, parseHex, toOklch } from "@/lib/color";
import {
  BASE_DARK,
  BASE_LIGHT,
  MAX_PALETTES,
  PALETTES_KEY,
  PALETTE_VERSION,
  THEME_VAR_KEYS,
  generatePalette,
  loadPalettes,
  makePalette,
  normalizePalette,
  upsertPalette,
  type Surface,
  type ThemeVars,
} from "@/lib/themePalette";
import { PRE_PAINT_SCRIPT, allThemes, applyTheme, currentScheme } from "@/lib/theme";
import { ThemeMaker } from "@/components/ThemeMaker";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";

const read = (p: string) => readFileSync(p, "utf8").replace(/\r\n/g, "\n");
const css = read(join(process.cwd(), "app", "globals.css"));

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
function over(fg: RGB, a: number, bg: RGB): RGB {
  return [0, 1, 2].map((i) => fg[i] * a + bg[i] * (1 - a)) as RGB;
}
const rgb = (vars: ThemeVars, k: string): RGB => vars[k].split(" ").map(Number) as RGB;

function cssBlock(selector: string): Record<string, string> {
  const i = css.indexOf(`\n${selector} {`);
  const body = css.slice(i, css.indexOf("}", i));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*(\d+\s+\d+\s+\d+)\s*;/g)) out[m[1]] = m[2].replace(/\s+/g, " ");
  return out;
}

const SURF = ["ink-950", "ink-900", "ink-850"];
const TONES = ["success", "danger", "warn", "info", "violet"];

/** Every readability rule the built-in themes are held to. */
function failures(vars: ThemeVars): string[] {
  const out: string[] = [];
  for (const s of SURF) {
    for (const k of ["accent-rgb", "accent-soft-rgb", "zinc-500", "zinc-600", ...TONES.map((t) => `tone-${t}`)]) {
      const r = ratio(rgb(vars, k), rgb(vars, s));
      if (r < 4.5) out.push(`${k} on ${s} = ${r.toFixed(2)}`);
    }
  }
  for (const fill of ["accent-rgb", "accent-soft-rgb"]) {
    const r = ratio(rgb(vars, "ink-950"), rgb(vars, fill));
    if (r < 4.5) out.push(`button label on ${fill} = ${r.toFixed(2)}`);
  }
  for (const t of TONES) {
    const ink = rgb(vars, `tone-${t}`);
    const r = ratio(ink, over(ink, 0.12, rgb(vars, "ink-950")));
    if (r < 4.5) out.push(`tone-${t} on its badge = ${r.toFixed(2)}`);
  }
  const c = (k: string) => ratio(rgb(vars, k), rgb(vars, "ink-850"));
  if (!(c("zinc-400") > c("zinc-500") && c("zinc-500") > c("zinc-600"))) out.push("zinc order");
  return out;
}

const PICKS = [
  ...Array.from({ length: 36 }, (_, i) => fromOklch({ l: 0.7, c: 0.15, h: i * 10 })),
  [255, 255, 255],
  [0, 0, 0],
  [119, 119, 119],
  [255, 255, 0],
  [0, 0, 255],
  [185, 28, 28],
  [253, 224, 71],
  [30, 41, 59],
] as RGB[];
const hex = (c: RGB) => `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
const SURFACES: Surface[] = ["neutral", "cool", "warm", "tinted"];

beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  const html = document.documentElement;
  delete html.dataset.theme;
  delete html.dataset.scheme;
  html.removeAttribute("style");
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("A. the generator starts from the vetted built-ins", () => {
  it("BASE_DARK is globals.css's :root and BASE_LIGHT is Daylight (mark1), value for value", () => {
    const root = cssBlock(":root");
    const day = { ...root, ...cssBlock(':root[data-theme="mark1"]') };
    for (const k of THEME_VAR_KEYS) {
      expect(BASE_DARK[k], `dark ${k}`).toBe(root[k]);
      expect(BASE_LIGHT[k], `light ${k}`).toBe(day[k]);
    }
    expect(THEME_VAR_KEYS.length).toBeGreaterThan(25);
  });
});

describe("B. every pick gives two readable versions", () => {
  it.each(SURFACES)("background %s: %#", (surface) => {
    const bad: string[] = [];
    for (const pick of PICKS) {
      for (const second of [null, "#b91c1c"]) {
        const g = generatePalette({ accent: hex(pick), second, surface });
        for (const scheme of ["dark", "light"] as const) {
          for (const f of failures(g.vars[scheme])) bad.push(`${hex(pick)} ${scheme}: ${f}`);
        }
      }
    }
    expect(bad).toEqual([]);
  });

  it("the dark version is dark and the light version is light", () => {
    for (const pick of PICKS) {
      const g = generatePalette({ accent: hex(pick), surface: "cool" });
      expect(lum(rgb(g.vars.dark, "ink-950"))).toBeLessThan(0.02);
      expect(lum(rgb(g.vars.light, "ink-950"))).toBeGreaterThan(0.8);
      expect(lum(rgb(g.vars.dark, "zinc-100"))).toBeGreaterThan(0.7);
      expect(lum(rgb(g.vars.light, "zinc-100"))).toBeLessThan(0.05);
    }
  });

  it("keeps the picked hue (a gold stays gold in both versions, only lighter or deeper)", () => {
    for (const pick of PICKS.slice(0, 36)) {
      const g = generatePalette({ accent: hex(pick), surface: "neutral" });
      const h0 = toOklch(pick).h;
      for (const scheme of ["dark", "light"] as const) {
        const o = toOklch(rgb(g.vars[scheme], "accent-rgb"));
        if (o.c < 0.03) continue; // pushed to near-white/black: no hue left to compare
        const d = Math.min(Math.abs(o.h - h0), 360 - Math.abs(o.h - h0));
        expect(d, `${hex(pick)} ${scheme}`).toBeLessThan(20);
      }
    }
  });

  it("a colour that already reads is used exactly as picked, and a note says when one moved", () => {
    const ok = generatePalette({ accent: "#22d3ee", surface: "cool" });
    expect(ok.vars.dark["accent-rgb"]).toBe("34 211 238");
    // Pale cyan is unreadable on white: the light version deepens it — and says so.
    expect(ok.vars.light["accent-rgb"]).not.toBe("34 211 238");
    expect(ok.notes.join(" ")).toMatch(/light version .* deeper/);
    expect(ok.notes.join(" ")).not.toMatch(/dark version/);
  });

  it("the second colour drives only the glow (accent-deep), never the main colour", () => {
    const a = generatePalette({ accent: "#f5b731", surface: "warm" });
    const b = generatePalette({ accent: "#f5b731", second: "#b91c1c", surface: "warm" });
    expect(b.vars.dark["accent-deep-rgb"]).toBe("185 28 28");
    expect(b.vars.dark["accent-rgb"]).toBe(a.vars.dark["accent-rgb"]);
    expect(b.vars.light["accent-rgb"]).toBe(a.vars.light["accent-rgb"]);
  });

  it("the background feel really changes the surfaces", () => {
    const of = (surface: Surface) => toOklch(rgb(generatePalette({ accent: "#ec4899", surface }).vars.dark, "ink-800"));
    expect(of("neutral").c).toBeLessThan(0.003);
    const warm = of("warm");
    const cool = of("cool");
    expect(Math.abs(warm.h - cool.h)).toBeGreaterThan(90);
    const tinted = of("tinted");
    const pink = toOklch(parseHex("#ec4899")!).h;
    expect(Math.min(Math.abs(tinted.h - pink), 360 - Math.abs(tinted.h - pink))).toBeLessThan(15);
  });
});

describe("C. applying a palette", () => {
  function save(mode: "dark" | "light" | "system", accent = "#a855f7") {
    const p = makePalette({ name: "Grape", accent, second: null, surface: "tinted", mode });
    expect(upsertPalette(p)).toBe(true);
    return p;
  }

  it("sets every variable inline, the scheme, the id, and remembers it", () => {
    const p = save("light");
    expect(applyTheme(p.id)?.name).toBe("Grape");
    const html = document.documentElement;
    expect(html.dataset.theme).toBe(p.id);
    expect(html.dataset.scheme).toBe("light");
    expect(currentScheme()).toBe("light");
    for (const k of THEME_VAR_KEYS) expect(html.style.getPropertyValue(`--${k}`), k).toBe(p.vars.light[k]);
    expect(html.style.getPropertyValue("color-scheme")).toBe("light");
    expect(localStorage.getItem("ij_theme")).toBe(p.id);
  });

  it("switching back to a Mark removes every inline variable (no palette bleeds through)", () => {
    const p = save("dark");
    applyTheme(p.id);
    applyTheme("mark1");
    const html = document.documentElement;
    expect(html.dataset.scheme).toBe("light");
    for (const k of THEME_VAR_KEYS) expect(html.style.getPropertyValue(`--${k}`), k).toBe("");
    applyTheme("mark2");
    expect(html.dataset.scheme).toBe("dark");
  });

  it("Match Windows follows the OS setting, live", () => {
    let light = true;
    const listeners: (() => void)[] = [];
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        get matches() {
          return light;
        },
        addEventListener: (_: string, fn: () => void) => listeners.push(fn),
        removeEventListener: vi.fn(),
      })),
    );
    const p = save("system");
    applyTheme(p.id);
    expect(document.documentElement.dataset.scheme).toBe("light");
    light = false;
    listeners.forEach((fn) => fn());
    expect(document.documentElement.dataset.scheme).toBe("dark");
    expect(document.documentElement.style.getPropertyValue("--ink-950")).toBe(p.vars.dark["ink-950"]);
  });

  it("an unknown or deleted palette id changes nothing", () => {
    applyTheme("mark2");
    expect(applyTheme("custom-nope")).toBeNull();
    expect(document.documentElement.dataset.theme).toBe("mark2");
  });

  it("the palette joins the theme list after the Marks", () => {
    const p = save("dark");
    const ids = allThemes().map((t) => t.id);
    expect(ids.slice(0, 5)).toEqual(["mark1", "mark2", "mark8", "mark23", "mark29"]);
    expect(ids).toContain(p.id);
  });
});

describe("D. the pre-paint script restores it before the first frame", () => {
  const run = () => new Function(PRE_PAINT_SCRIPT)();

  it("a saved palette: variables, scheme and id, from storage alone", () => {
    const p = makePalette({ name: "Night", accent: "#10b981", second: null, surface: "cool", mode: "dark" });
    localStorage.setItem(PALETTES_KEY, JSON.stringify([p]));
    localStorage.setItem("ij_theme", p.id);
    run();
    const html = document.documentElement;
    expect(html.dataset.theme).toBe(p.id);
    expect(html.dataset.scheme).toBe("dark");
    expect(html.style.getPropertyValue("--accent-rgb")).toBe(p.vars.dark["accent-rgb"]);
    expect(html.style.getPropertyValue("--ink-950")).toBe(p.vars.dark["ink-950"]);
  });

  it("the light Marks get the light scheme; the dark ones and no choice get dark", () => {
    for (const [id, scheme] of [["mark8", "light"], ["mark1", "light"], ["mark23", "dark"]] as const) {
      localStorage.setItem("ij_theme", id);
      run();
      expect(document.documentElement.dataset.theme).toBe(id);
      expect(document.documentElement.dataset.scheme, id).toBe(scheme);
    }
    localStorage.clear();
    delete document.documentElement.dataset.theme;
    run();
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(document.documentElement.dataset.scheme).toBe("dark");
  });

  it("a missing palette falls back to the default; a bad value or bad JSON is skipped, never thrown", () => {
    localStorage.setItem("ij_theme", "custom-gone");
    localStorage.setItem(PALETTES_KEY, "[]");
    run();
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(document.documentElement.dataset.scheme).toBe("dark");

    const p = makePalette({ name: "X", accent: "#ef4444", second: null, surface: "warm", mode: "dark" });
    const evil = { ...p, vars: { ...p.vars, dark: { ...p.vars.dark, "accent-rgb": "red; background: url(x)" } } };
    localStorage.setItem(PALETTES_KEY, JSON.stringify([evil]));
    localStorage.setItem("ij_theme", p.id);
    run();
    expect(document.documentElement.style.getPropertyValue("--accent-rgb")).toBe("");
    expect(document.documentElement.style.getPropertyValue("--ink-950")).toBe(p.vars.dark["ink-950"]);

    localStorage.setItem(PALETTES_KEY, "{not json");
    expect(run).not.toThrow();
  });
});

describe("E. storage", () => {
  it("rejects what cannot be a palette and recomputes an older generator's colours", () => {
    expect(normalizePalette({ id: "../../x", accent: "#ffffff" })).toBeNull();
    expect(normalizePalette({ id: "custom-a", accent: "blue" })).toBeNull();
    const old = normalizePalette({ id: "custom-a", accent: "#ff0000", v: 0, vars: { dark: {}, light: {} } });
    expect(old?.v).toBe(PALETTE_VERSION);
    expect(old?.vars.dark["accent-rgb"]).toBe(generatePalette({ accent: "#ff0000", surface: "cool" }).vars.dark["accent-rgb"]);
  });

  it(`keeps at most ${MAX_PALETTES} palettes`, () => {
    for (let i = 0; i < MAX_PALETTES; i++) {
      expect(upsertPalette(makePalette({ name: `T${i}`, accent: "#22d3ee", second: null, surface: "cool", mode: "dark" }))).toBe(true);
    }
    expect(upsertPalette(makePalette({ name: "one more", accent: "#22d3ee", second: null, surface: "cool", mode: "dark" }))).toBe(false);
    expect(loadPalettes()).toHaveLength(MAX_PALETTES);
  });
});

describe("F. the light rules are keyed on the scheme, not on Mark ids", () => {
  it("no light re-ink selector names mark1/mark8 any more; the generator emits the scheme selector", () => {
    const from = css.indexOf("/* light-amber-overrides:start */") - 2000;
    const tail = css.slice(Math.max(0, from));
    expect(tail).not.toMatch(/:root\[data-theme="mark(1|8)"\] \./);
    expect((css.match(/:root\[data-scheme="light"\]/g) ?? []).length).toBeGreaterThan(190);
    const gen = read(join(process.cwd(), "..", "scripts", "gen_light_tones.py"));
    expect(gen).toContain(`LIGHT = ':root[data-scheme="light"]'`);
    expect(gen).not.toContain(`':root[data-theme="mark1"]`);
  });

  it("anti-vacuity: the Mark palette blocks and Liquid Glass's own glass styling stay keyed on their ids", () => {
    expect(css).toContain(':root[data-theme="mark1"] {');
    expect(css).toContain(':root[data-theme="mark8"] {');
    expect(css).toContain(':root[data-theme="mark8"] .card-surface');
  });
});

describe("G. Settings → Appearance: the theme maker", () => {
  it("makes a theme: pick a colour, see both versions, save — it is applied and listed", async () => {
    render(<ThemeMaker />);
    fireEvent.click(screen.getByTestId("theme-maker-new"));
    const editor = screen.getByTestId("theme-maker-editor");
    fireEvent.change(within(editor).getByLabelText("Name"), { target: { value: "Forest" } });
    fireEvent.click(within(screen.getByTestId("theme-maker-accent")).getByRole("button", { name: "Use #10b981" }));
    fireEvent.click(within(screen.getByTestId("theme-maker-surface")).getByRole("radio", { name: "Warm" }));
    fireEvent.click(within(screen.getByTestId("theme-maker-mode")).getByRole("radio", { name: "Light" }));
    expect(screen.getByTestId("theme-preview-dark")).toBeTruthy();
    expect(screen.getByTestId("theme-preview-light").style.getPropertyValue("--ink-950")).not.toBe("");
    expect(screen.getByTestId("theme-maker-readability").textContent).toMatch(/Easy to read in both versions/);

    await act(async () => {
      fireEvent.click(within(editor).getByRole("button", { name: /Save and use/ }));
    });
    const [p] = loadPalettes();
    expect(p).toMatchObject({ name: "Forest", accent: "#10b981", surface: "warm", mode: "light" });
    expect(document.documentElement.dataset.theme).toBe(p.id);
    expect(document.documentElement.dataset.scheme).toBe("light");
    expect(screen.queryByTestId("theme-maker-editor")).toBeNull();
    expect(within(screen.getByTestId(`theme-row-${p.id}`)).getByText("In use")).toBeTruthy();
  });

  it("a bad hex code is refused in words and Save waits for a real colour", () => {
    render(<ThemeMaker />);
    fireEvent.click(screen.getByTestId("theme-maker-new"));
    fireEvent.change(screen.getByLabelText("Main colour — hex code"), { target: { value: "#12" } });
    expect(screen.getByText(/six hex digits/)).toBeTruthy();
    // The last GOOD colour is still the one previewed and saved.
    expect(screen.getByTestId("theme-preview-dark")).toBeTruthy();
  });

  it("delete takes two presses; deleting the theme in use returns to the default", async () => {
    const p = makePalette({ name: "Temp", accent: "#ef4444", second: null, surface: "neutral", mode: "dark" });
    upsertPalette(p);
    applyTheme(p.id);
    render(<ThemeMaker />);
    const row = await screen.findByTestId(`theme-row-${p.id}`);
    const del = within(row).getByRole("button", { name: "Delete" });
    fireEvent.click(del);
    expect(loadPalettes()).toHaveLength(1);
    await act(async () => {
      fireEvent.click(within(row).getByRole("button", { name: "Confirm?" }));
    });
    expect(loadPalettes()).toHaveLength(0);
    expect(document.documentElement.dataset.theme).toBe("mark2");
    expect(screen.queryByTestId(`theme-row-${p.id}`)).toBeNull();
  });

  it("Edit reopens the picks; Cancel leaves the saved theme as it was", async () => {
    const p = makePalette({ name: "Keep", accent: "#6366f1", second: "#ec4899", surface: "cool", mode: "system" });
    upsertPalette(p);
    render(<ThemeMaker />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Keep" }));
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Keep");
    expect((screen.getByLabelText("Main colour — hex code") as HTMLInputElement).value).toBe("#6366f1");
    expect(screen.getByTestId("theme-maker-second")).toBeTruthy();
    fireEvent.click(within(screen.getByTestId("theme-maker-accent")).getByRole("button", { name: "Use #ef4444" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(loadPalettes()[0].accent).toBe("#6366f1");
  });

  it("the title bar's theme row shows a saved palette as its own dot, and it applies", async () => {
    render(<ThemeSwitcher />);
    const p = makePalette({ name: "Dusk", accent: "#f97316", second: null, surface: "warm", mode: "dark" });
    await act(async () => {
      upsertPalette(p);
    });
    const dot = screen.getByRole("button", { name: /Your theme — Dusk/ });
    fireEvent.click(dot);
    expect(document.documentElement.dataset.theme).toBe(p.id);
    // The five Marks are all still there (anti-vacuity).
    for (const n of ["Daylight", "Arc Cyan", "Liquid Glass", "Gold & Red", "Silver & Red"]) {
      expect(screen.getByRole("button", { name: new RegExp(n) })).toBeTruthy();
    }
  });
});
