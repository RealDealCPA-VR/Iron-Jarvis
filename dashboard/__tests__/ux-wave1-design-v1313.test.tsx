/**
 * UX wave 1, track T2 (design system) — v1.313.0. "Foundation & trust".
 *
 * Every view should read calmly and honestly in EVERY theme. The verified
 * findings (scratchpad ux_wave1.json, track T2-design-system) that this file
 * pins, each as one user-visible behaviour:
 *
 *   A. TONE TOKENS (U1-2). `--tone-success|danger|warn|info|violet` as
 *      space-separated RGB in the dark default AND separately in Daylight
 *      (mark1) and Liquid Glass (mark8); Tailwind exposes them as
 *      `tone-*` colour groups (never a bare `violet`/`success` group — that
 *      would shadow Tailwind's palette). Contrast is COMPUTED here from the
 *      values in globals.css, never asserted by eye.
 *   B. THE CENTRAL LIGHT OVERRIDE BLOCK. Like the v1.233.0 amber block: every
 *      pale emerald/rose/sky/violet/cyan/teal/lime/… text utility in use gets
 *      a mark1 + mark8 rule whose ink clears 4.5:1 on the light surfaces and
 *      on its own badge tint; tints/borders in use get a rule that is never
 *      fainter than the dark one. Scoped to the light Marks only; amber stays
 *      in its own block (one system per hue).
 *   C. PRIMITIVES ON THE TOKENS. Badge, its dot, StatusIcon, Dot, ErrorNote,
 *      SuccessNote, MockChip, ConfirmButton (still two presses) and
 *      OriginChip read through the tokens.
 *   D. THE LIGHT RAMP. zinc-600 (hints, placeholders, timestamps) and
 *      accent-soft (links, active tabs) clear AA on the light Marks — mark1
 *      and mark8 picked separately — with the zinc ramp order preserved, and a
 *      primary button's label still reads on its fill and its hover fill.
 *   E. THE WORKFLOW CANVAS follows the theme LIVE (React Flow colorMode +
 *      token-driven minimap/background), instead of a black slab in light.
 *   F. NO BLACK SLABS. The Build stage, the self-dev callout and the
 *      updates/webhooks command chips stop using `bg-black/…` (black is the
 *      one neutral the theme system does not remap) — plus a ratchet on the
 *      remaining `<code … bg-black/…>` chips.
 *   G. `.code-inline` + `<Code>` and the `<Button variant size>` primitive
 *      (U1-3), with ratchets so ad-hoc sizing never grows.
 *   H. MODAL FOCUS: focus moves in, Tab is trapped (disabled/hidden skipped),
 *      focus returns to the opener, a `z` prop — and Escape/backdrop/busy
 *      dismissal is EXACTLY as before.
 *   I. REDUCED MOTION: every `m.*` honours the OS setting (MotionConfig
 *      reducedMotion="user").
 *   J. HELPER TEXT on Build and Computer use: the 12px `meta` step and
 *      zinc-500, for helper paragraphs only.
 *   K. Dark brand marks (Anthropic, GitHub…) draw in currentColor so they
 *      invert on the light Marks; bright brand colours keep their hex.
 *
 * Anti-vacuity controls run beside each group: a real status still names its
 * value, the honest "offline mock" chip is still there, the destructive button
 * still takes two presses, the default theme still draws the dark canvas,
 * every Mark block still exists, the amber block is untouched.
 *
 * Source pins read files with CRLF normalised (the CI runner checks out CRLF).
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  useContext,
  useState,
  type ComponentType,
  type ReactElement,
  type ReactNode,
} from "react";

/* ---- mocks (only the workflow canvas needs them) -------------------------- */

const { getMock, postMock, rf } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  rf: {
    flow: [] as Record<string, unknown>[],
    mini: [] as Record<string, unknown>[],
    bg: [] as Record<string, unknown>[],
  },
}));

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: getMock,
    post: postMock,
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
  };
});

vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));

vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (text: string, chunk: string) => (text ? `${text} ${chunk}` : chunk),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

// jsdom cannot lay out React Flow; record the props the canvas hands it.
vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  const { createElement } = await import("react");
  return {
    ...actual,
    ReactFlow: (p: Record<string, unknown> & { children?: ReactNode }) => {
      rf.flow.push(p);
      return createElement("div", { "data-testid": "rf-canvas" }, p.children);
    },
    ReactFlowProvider: ({ children }: { children?: ReactNode }) =>
      createElement("div", null, children),
    Background: (p: Record<string, unknown>) => {
      rf.bg.push(p);
      return null;
    },
    Controls: () => null,
    MiniMap: (p: Record<string, unknown>) => {
      rf.mini.push(p);
      return null;
    },
    useReactFlow: () => ({ fitView: async () => false }),
  };
});

import { MotionConfigContext } from "framer-motion";
import tailwindConfig from "@/tailwind.config";
import * as ui from "@/components/ui";
import { Modal } from "@/components/Modal";
import { MotionProvider } from "@/components/MotionProvider";
import OriginChip from "@/components/sessions/OriginChip";
import { ProviderMark } from "@/components/BrandGlyph";
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas";

/* ---- shared helpers ------------------------------------------------------- */

const ROOT = process.cwd();
const read = (...p: string[]) => readFileSync(join(ROOT, ...p), "utf8").replace(/\r\n/g, "\n");
const css = read("app", "globals.css");

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (name === "__tests__" || name === "node_modules" || name === ".next") continue;
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(tsx?|jsx?)$/.test(name)) out.push(p);
  }
  return out;
}
const SOURCES: { path: string; text: string }[] = ["app", "components", "lib"]
  .flatMap((d) => walk(join(ROOT, d)))
  .map((p) => ({ path: p.replace(/\\/g, "/"), text: readFileSync(p, "utf8").replace(/\r\n/g, "\n") }));

type RGB = [number, number, number];

/** The declarations of ONE theme block, e.g. `:root[data-theme="mark1"] {`. */
function themeBlock(selector: string): string {
  const i = css.indexOf(`\n${selector} {`);
  if (i < 0) return "";
  return css.slice(i, css.indexOf("}", i));
}
function varsOf(block: string): Record<string, RGB> {
  const out: Record<string, RGB> = {};
  for (const m of block.matchAll(/--([a-z0-9-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s*;/g)) {
    out[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return out;
}
const RAW = {
  root: varsOf(themeBlock(":root")),
  mark1: varsOf(themeBlock(':root[data-theme="mark1"]')),
  mark8: varsOf(themeBlock(':root[data-theme="mark8"]')),
  mark23: varsOf(themeBlock(':root[data-theme="mark23"]')),
  mark29: varsOf(themeBlock(':root[data-theme="mark29"]')),
};
/** Effective variables per theme (a Mark inherits what it does not set). */
const THEME: Record<"dark" | "mark1" | "mark8" | "mark23" | "mark29", Record<string, RGB>> = {
  dark: RAW.root,
  mark1: { ...RAW.root, ...RAW.mark1 },
  mark8: { ...RAW.root, ...RAW.mark8 },
  mark23: { ...RAW.root, ...RAW.mark23 },
  mark29: { ...RAW.root, ...RAW.mark29 },
};
const LIGHT = ["mark1", "mark8"] as const;

function lum([r, g, b]: RGB): number {
  const f = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function contrast(a: RGB, b: RGB): number {
  const x = lum(a);
  const y = lum(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
/** `fg` at `alpha` painted over an opaque `bg`. */
function over(fg: RGB, alpha: number, bg: RGB): RGB {
  return [0, 1, 2].map((i) => fg[i] * alpha + bg[i] * (1 - alpha)) as RGB;
}
const fmt = (n: number) => n.toFixed(2);

/** The surfaces text sits on in a theme: page, recessed panel, card. On the
 *  light Marks the PAGE (ink-950) is the darkest, i.e. the hardest for dark
 *  ink; mark8's frosted .card-surface is a blend between it and white, so
 *  measuring ink-950 bounds the frosted card too. */
const SURFACES = ["ink-950", "ink-900", "ink-850"] as const;

const AA = 4.5;

afterEach(() => {
  cleanup();
  delete document.documentElement.dataset.theme;
  document.body.style.overflow = "";
});

/* ========================================================================== */
/*  A. Tone tokens (U1-2)                                                      */
/* ========================================================================== */

const TONES = ["success", "danger", "warn", "info", "violet"] as const;
/** Badge tints the primitives may use on a tone surface (bg-tone-x/10, the
 *  error/success notes' ~/7). Inks are measured against the strongest. */
const TONE_TINT = 0.12;

describe("A. tone tokens — one semantic colour per meaning, per theme", () => {
  it("every Mark block still exists (anti-vacuity: no theme was dropped)", () => {
    for (const key of ["root", "mark1", "mark8", "mark23", "mark29"] as const) {
      expect(Object.keys(RAW[key]).length, `${key} block`).toBeGreaterThan(3);
    }
  });

  it("defines --tone-* as space-separated RGB in the dark default AND separately in mark1 and mark8", () => {
    const missing: string[] = [];
    for (const t of TONES) {
      for (const key of ["root", "mark1", "mark8"] as const) {
        if (!RAW[key][`tone-${t}`]) missing.push(`${key} --tone-${t}`);
      }
    }
    expect(missing, "declare these as `--tone-x: R G B;` in the theme blocks").toEqual([]);
  });

  it("Tailwind exposes them as tone-* groups with alpha, never as a bare palette name", () => {
    const colors = ((tailwindConfig.theme?.extend as Record<string, unknown> | undefined)
      ?.colors ?? {}) as Record<string, unknown>;
    const tone = (colors.tone ?? {}) as Record<string, unknown>;
    for (const t of TONES) {
      expect(tone[t], `colors.tone.${t}`).toBe(`rgb(var(--tone-${t}) / <alpha-value>)`);
    }
    // A `violet` (or `success`…) group would SHADOW Tailwind's own palette and
    // break every bg-violet-500/10 already in the app.
    for (const bad of ["violet", "success", "danger", "warn", "info", "emerald", "rose"]) {
      expect(colors[bad], `colors.${bad} must not be redefined`).toBeUndefined();
    }
  });

  it("the light inks clear 4.5:1 on every light surface and on their own badge tint", () => {
    const fails: string[] = [];
    for (const th of LIGHT) {
      const v = THEME[th];
      for (const t of TONES) {
        const fg = v[`tone-${t}`];
        if (!fg) {
          fails.push(`${th} --tone-${t} missing`);
          continue;
        }
        for (const s of SURFACES) {
          const r = contrast(fg, v[s]);
          if (r < AA) fails.push(`${th} tone-${t} on ${s} = ${fmt(r)}`);
        }
        const tint = over(fg, TONE_TINT, v["ink-950"]);
        const r = contrast(fg, tint);
        if (r < AA) fails.push(`${th} tone-${t} on its /${TONE_TINT * 100} tint = ${fmt(r)}`);
      }
    }
    expect(fails).toEqual([]);
  });

  it("the dark default keeps a PALE ink that reads on the dark surfaces (dark Marks look as before)", () => {
    for (const t of TONES) {
      const dark = THEME.dark[`tone-${t}`];
      const light = THEME.mark1[`tone-${t}`];
      expect(dark, `--tone-${t}`).toBeDefined();
      expect(light, `mark1 --tone-${t}`).toBeDefined();
      if (!dark || !light) continue;
      for (const s of ["ink-950", "ink-850"] as const) {
        expect(contrast(dark, THEME.dark[s]), `dark tone-${t} on ${s}`).toBeGreaterThanOrEqual(AA);
      }
      // Pale in dark, deep in light — the same token flips, it is not one ink.
      expect(lum(dark), `tone-${t}: dark value must be lighter than Daylight's`).toBeGreaterThan(
        lum(light),
      );
    }
  });
});

/* ========================================================================== */
/*  B. The central light override block for raw pale utilities                */
/* ========================================================================== */

const LSTART = "/* light-tone-overrides:start */";
const LEND = "/* light-tone-overrides:end */";
const HUES =
  "emerald|green|rose|red|sky|blue|violet|purple|fuchsia|pink|indigo|cyan|teal|lime";
const HUE_500: Record<string, RGB> = {
  emerald: [16, 185, 129],
  green: [34, 197, 94],
  rose: [244, 63, 94],
  red: [239, 68, 68],
  sky: [14, 165, 233],
  blue: [59, 130, 246],
  violet: [139, 92, 246],
  purple: [168, 85, 247],
  fuchsia: [217, 70, 239],
  pink: [236, 72, 153],
  indigo: [99, 102, 241],
  cyan: [6, 182, 212],
  teal: [20, 184, 166],
  lime: [132, 204, 22],
};

function alphaOf(suffix: string | undefined): number | null {
  if (!suffix) return null;
  const s = suffix.slice(1);
  if (s.startsWith("[")) return Number(s.slice(1, -1));
  return Number(s) / 100;
}

/** The utilities the block must cover, read from the source like the amber
 *  test does: pale TEXT (shade <= 400) always; BORDER/RING tints (300-500,
 *  alpha <= .5); BG tints (300-500, alpha < .5). Solid indicator dots and
 *  saturated fills are left alone, as amber leaves them. */
const usedTone = new Set<string>();
for (const { text } of SOURCES) {
  const re = new RegExp(
    `\\b(text|border|ring|bg)-(${HUES})-(\\d{2,3})(\\/(?:\\d{1,3}|\\[[0-9.]+\\]))?`,
    "g",
  );
  for (const m of text.matchAll(re)) {
    const [whole, kind, , shadeS, suffix] = m;
    const shade = Number(shadeS);
    const a = alphaOf(suffix);
    if (kind === "text" && shade <= 400) usedTone.add(whole);
    else if ((kind === "border" || kind === "ring") && shade >= 300 && shade <= 500 && a !== null && a <= 0.5)
      usedTone.add(whole);
    else if (kind === "bg" && shade >= 300 && shade <= 500 && a !== null && a < 0.5) usedTone.add(whole);
  }
}

type Decl = { prop: string; rgb: RGB; a: number };
/** (theme, class) -> declaration, parsed from the generated block. */
function parseOverrideBlock(block: string): Map<string, Decl> {
  const out = new Map<string, Decl>();
  for (const chunk of block.split("}")) {
    const brace = chunk.indexOf("{");
    if (brace < 0) continue;
    const sels = chunk.slice(0, brace).replace(/\/\*[\s\S]*?\*\//g, "");
    const decl = chunk.slice(brace + 1);
    const d = decl.match(
      /(color|background-color|border-color|--tw-ring-color):\s*rgb\((\d+)\s+(\d+)\s+(\d+)(?:\s*\/\s*([0-9.]+))?\)/,
    );
    if (!d) continue;
    const parsed: Decl = {
      prop: d[1],
      rgb: [Number(d[2]), Number(d[3]), Number(d[4])],
      a: d[5] === undefined ? 1 : Number(d[5]),
    };
    for (const raw of sels.split(",")) {
      const sm = raw.trim().match(/^:root\[data-theme="(mark1|mark8)"\] \.(\S+)$/);
      if (!sm) continue;
      out.set(`${sm[1]} ${sm[2].replace(/\\(.)/g, "$1")}`, parsed);
    }
  }
  return out;
}

const lStart = css.indexOf(LSTART);
const lEnd = css.indexOf(LEND);
const lightBlock = lStart >= 0 && lEnd > lStart ? css.slice(lStart, lEnd) : "";
const lightRules = parseOverrideBlock(lightBlock);

describe("B. raw pale hues read on the light Marks (generated block)", () => {
  it("has the generated block with start/end markers", () => {
    expect(lStart, `globals.css needs ${LSTART}`).toBeGreaterThan(-1);
    expect(lEnd).toBeGreaterThan(lStart);
  });

  it("covers every pale text, tint, border and ring utility in use, for mark1 AND mark8", () => {
    const missing = [...usedTone].filter(
      (c) => !lightRules.has(`mark1 ${c}`) || !lightRules.has(`mark8 ${c}`),
    );
    expect(missing.sort(), "add these to the light-tone block in globals.css").toEqual([]);
    // Anti-vacuity: the scan really sees the app (rose-300 alone has ~135 sites).
    expect(usedTone.size).toBeGreaterThan(80);
    expect(usedTone.has("text-rose-300")).toBe(true);
    expect(usedTone.has("text-emerald-300")).toBe(true);
    expect(usedTone.has("bg-rose-500/[0.1]")).toBe(true);
  });

  it("every TEXT rule is a deep ink: >= 4.5:1 on each light surface and on its hue's badge tint", () => {
    const fails: string[] = [];
    let checked = 0;
    for (const [key, decl] of lightRules) {
      const [th, cls] = key.split(" ") as ["mark1" | "mark8", string];
      if (!cls.startsWith("text-")) continue;
      checked++;
      const v = THEME[th];
      for (const s of SURFACES) {
        const bg = v[s];
        const r = contrast(over(decl.rgb, decl.a, bg), bg);
        if (r < AA) fails.push(`${th} .${cls} on ${s} = ${fmt(r)}`);
      }
      // The badge case: text on bg-<hue>-500/10 (its light override, else the
      // raw 10% tint) over the page.
      const hue = cls.split("-")[1];
      const tintRule = lightRules.get(`${th} bg-${hue}-500/10`);
      const tint = tintRule
        ? over(tintRule.rgb, tintRule.a, v["ink-950"])
        : over(HUE_500[hue] ?? [128, 128, 128], 0.1, v["ink-950"]);
      const r = contrast(over(decl.rgb, decl.a, tint), tint);
      if (r < AA) fails.push(`${th} .${cls} on bg-${hue}-500/10 = ${fmt(r)}`);
    }
    expect(checked, "no text rules parsed").toBeGreaterThan(40);
    expect(fails).toEqual([]);
  });

  it("tints and borders get MORE visible on light, never fainter — and tints stay tints", () => {
    const fails: string[] = [];
    let checked = 0;
    for (const [key, decl] of lightRules) {
      const cls = key.split(" ")[1];
      const m = cls.match(/^(bg|border|ring)-[a-z]+-\d{2,3}(\/.+)$/);
      if (!m) continue;
      checked++;
      const original = alphaOf(m[2]) ?? 1;
      if (decl.a + 1e-9 < original) fails.push(`${key}: ${decl.a} < original ${original}`);
      if (m[1] === "bg" && decl.a > 0.4) fails.push(`${key}: tint alpha ${decl.a} > 0.4`);
    }
    expect(checked, "no tint/border rules parsed").toBeGreaterThan(80);
    expect(fails).toEqual([]);
  });

  it("is scoped to the light Marks only and leaves amber to its own block (one system per hue)", () => {
    expect(lightBlock.length).toBeGreaterThan(0);
    for (const line of lightBlock.split("\n")) {
      if (line.includes("{")) expect(line.startsWith(':root[data-theme="mark'), line).toBe(true);
    }
    expect(lightBlock).not.toMatch(/-(amber|orange|yellow)-\d/);
  });

  it("the v1.233.0 amber block is still there (anti-vacuity: not folded away half-done)", () => {
    expect(css).toContain("/* light-amber-overrides:start */");
    expect(css).toMatch(/\.text-amber-300 \{ color: rgb\(146 64 14 \/ 1\); \}/);
  });
});

/* ========================================================================== */
/*  C. Primitives point at the tokens                                          */
/* ========================================================================== */

const NO_RAW_HUE = /\b(text|bg|border)-(emerald|rose|violet|sky|amber)-\d/;

describe("C. status primitives read through the tone tokens", () => {
  it("Badge: completed / failed / pending / violet use tone-* for the pill AND its dot", () => {
    const cases: [string, string, ui.Tone | undefined][] = [
      ["completed", "success", undefined],
      ["failed", "danger", undefined],
      ["pending", "warn", undefined],
      ["tool", "violet", undefined],
    ];
    for (const [value, tone, explicit] of cases) {
      render(<ui.Badge value={value} tone={explicit} />);
      const pill = screen.getByText(value);
      expect(pill.className, `${value} pill`).toContain(`text-tone-${tone}`);
      expect(pill.className, `${value} pill`).toContain(`bg-tone-${tone}`);
      expect(pill.className, `${value} pill`).toContain(`border-tone-${tone}`);
      expect(pill.className, `${value} pill`).not.toMatch(NO_RAW_HUE);
      const dot = pill.querySelector("span");
      expect(dot?.className ?? "", `${value} dot`).toContain(`bg-tone-${tone}`);
      cleanup();
    }
  });

  it("anti-vacuity: running stays accent, idle stays neutral, and the value is still shown", () => {
    render(<ui.Badge value="running" />);
    expect(screen.getByText("running").className).toContain("text-accent-soft");
    cleanup();
    render(<ui.Badge value="idle" />);
    expect(screen.getByText("idle").className).toContain("text-zinc-300");
  });

  it("StatusIcon, StatusDot and Dot use the tokens for success/failure", () => {
    const { container: ok } = render(<ui.StatusIcon status="completed" />);
    expect(ok.querySelector("svg")?.getAttribute("class") ?? "").toContain("text-tone-success");
    cleanup();
    const { container: bad } = render(<ui.StatusIcon status="failed" />);
    expect(bad.querySelector("svg")?.getAttribute("class") ?? "").toContain("text-tone-danger");
    cleanup();
    const { container: sd } = render(<ui.StatusDot status="failed" />);
    expect(sd.querySelector("span")?.className ?? "").toContain("bg-tone-danger");
    cleanup();
    const { container: on } = render(<ui.Dot on />);
    expect(on.querySelector("span")?.className ?? "").toContain("bg-tone-success");
  });

  it("ErrorNote is an alert in tone-danger; SuccessNote a polite status in tone-success", () => {
    render(<ui.ErrorNote>Could not save.</ui.ErrorNote>);
    const err = screen.getByRole("alert");
    expect(err).toHaveTextContent("Could not save.");
    expect(err.className).toContain("text-tone-danger");
    expect(err.className).not.toMatch(NO_RAW_HUE);
    cleanup();
    render(<ui.SuccessNote>Saved.</ui.SuccessNote>);
    const ok = screen.getByRole("status");
    expect(ok).toHaveTextContent("Saved.");
    expect(ok.className).toContain("text-tone-success");
    expect(ok.className).not.toMatch(NO_RAW_HUE);
  });

  it("MockChip still discloses the offline mock (words + title) and reads in tone-warn", () => {
    render(<ui.MockChip />);
    const chip = screen.getByText(/offline mock/);
    expect(chip.getAttribute("title")).toMatch(/offline mock model/);
    expect(chip.className).toContain("text-tone-warn");
    expect(chip.className).not.toMatch(NO_RAW_HUE);
  });

  it("ConfirmButton still takes TWO presses, and its armed state reads in tone-danger", async () => {
    const onConfirm = vi.fn();
    render(<ui.ConfirmButton onConfirm={onConfirm} label="Delete" />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const armed = screen.getByRole("button", { name: "Confirm?" });
    expect(onConfirm).not.toHaveBeenCalled();
    expect(armed.className).toContain("text-tone-danger");
    expect(armed.className).not.toMatch(NO_RAW_HUE);
    await act(async () => {
      fireEvent.click(armed);
    });
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("OriginChip maps its kinds onto the tokens and still names who started the session", () => {
    const cases: [string, string][] = [
      ["schedule:nightly-brief", "info"],
      ["comm:telegram", "success"],
      ["autonomy", "warn"],
      ["reflex:on-mail", "violet"],
      ["self_dev", "danger"],
    ];
    for (const [origin, tone] of cases) {
      render(<OriginChip origin={origin} />);
      const chip = screen.getByTestId("origin-chip");
      expect(chip).toHaveTextContent(origin);
      expect(chip.getAttribute("title")).toBe(`Started by: ${origin}`);
      expect(chip.className, origin).toContain(`text-tone-${tone}`);
      expect(chip.className, origin).not.toMatch(NO_RAW_HUE);
      cleanup();
    }
    // An absent origin is still NOTHING, never a guessed chip.
    const { container } = render(<OriginChip origin="" />);
    expect(container).toBeEmptyDOMElement();
  });
});

/* ========================================================================== */
/*  D. The light ramp (zinc-600, accent-soft, the primary button)              */
/* ========================================================================== */

describe("D. hints, links and buttons clear AA on the light Marks", () => {
  it("zinc-600 (hints, placeholders, timestamps) >= 4.5:1 on page, panel and card in mark1 AND mark8", () => {
    const fails: string[] = [];
    for (const th of LIGHT) {
      for (const s of SURFACES) {
        const r = contrast(THEME[th]["zinc-600"], THEME[th][s]);
        if (r < AA) fails.push(`${th} zinc-600 on ${s} = ${fmt(r)}`);
      }
    }
    expect(fails).toEqual([]);
  });

  it("the zinc ramp keeps its order (400 > 500 > 600 in contrast) in every theme", () => {
    for (const th of ["dark", "mark1", "mark8"] as const) {
      const v = THEME[th];
      const c = (k: string) => contrast(v[k], v["ink-850"]);
      expect(c("zinc-400"), `${th} 400 vs 500`).toBeGreaterThan(c("zinc-500"));
      expect(c("zinc-500"), `${th} 500 vs 600`).toBeGreaterThan(c("zinc-600"));
    }
  });

  it("zinc-500 (the helper-text step) >= 4.5:1 on page and card in dark, mark1 and mark8", () => {
    const fails: string[] = [];
    for (const th of ["dark", "mark1", "mark8"] as const) {
      for (const s of ["ink-950", "ink-850"] as const) {
        const r = contrast(THEME[th]["zinc-500"], THEME[th][s]);
        if (r < AA) fails.push(`${th} zinc-500 on ${s} = ${fmt(r)}`);
      }
    }
    expect(fails).toEqual([]);
  });

  it("accent-soft (links, active tabs) >= 4.5:1 in mark1 and mark8, each Mark keeping its own hue", () => {
    const fails: string[] = [];
    for (const th of LIGHT) {
      expect(RAW[th]["accent-soft-rgb"], `${th} sets its own accent-soft`).toBeDefined();
      for (const s of SURFACES) {
        const r = contrast(THEME[th]["accent-soft-rgb"], THEME[th][s]);
        if (r < AA) fails.push(`${th} accent-soft on ${s} = ${fmt(r)}`);
      }
    }
    expect(fails).toEqual([]);
    // Picked separately: Liquid Glass stays system blue, Daylight stays cyan.
    expect(THEME.mark8["accent-soft-rgb"]).not.toEqual(THEME.mark1["accent-soft-rgb"]);
  });

  it("a primary button's label (ink-950) reads on its accent fill and its accent-soft hover fill", () => {
    const fails: string[] = [];
    for (const th of LIGHT) {
      const label = THEME[th]["ink-950"];
      for (const fill of ["accent-rgb", "accent-soft-rgb"]) {
        const r = contrast(label, THEME[th][fill]);
        if (r < AA) fails.push(`${th} label on ${fill} = ${fmt(r)}`);
      }
    }
    expect(fails).toEqual([]);
  });
});

/* ========================================================================== */
/*  E. The workflow canvas follows the theme, live                             */
/* ========================================================================== */

const lastFlow = () => rf.flow[rf.flow.length - 1] ?? {};
const lastMini = () => rf.mini[rf.mini.length - 1] ?? {};
const lastBg = () => rf.bg[rf.bg.length - 1] ?? {};

describe("E. the workflow canvas is not a black slab in light themes", () => {
  beforeEach(() => {
    rf.flow.length = 0;
    rf.mini.length = 0;
    rf.bg.length = 0;
    getMock.mockReset();
    postMock.mockReset();
    getMock.mockImplementation(async () => ({ workflows: [] }));
    localStorage.clear();
  });

  it("Daylight (mark1) draws React Flow in light mode", async () => {
    document.documentElement.dataset.theme = "mark1";
    render(<WorkflowCanvas />);
    await screen.findByTestId("rf-canvas");
    await waitFor(() => expect(lastFlow().colorMode).toBe("light"));
  });

  it("follows a LIVE theme switch both ways (the switcher flips data-theme on <html>)", async () => {
    document.documentElement.dataset.theme = "mark2";
    render(<WorkflowCanvas />);
    await screen.findByTestId("rf-canvas");
    await waitFor(() => expect(lastFlow().colorMode).toBe("dark"));
    act(() => {
      document.documentElement.dataset.theme = "mark8";
    });
    await waitFor(() => expect(lastFlow().colorMode).toBe("light"));
    act(() => {
      document.documentElement.dataset.theme = "mark23";
    });
    await waitFor(() => expect(lastFlow().colorMode).toBe("dark"));
  });

  it("anti-vacuity: the default (no data-theme) keeps the dark canvas and the toolbar is all there", async () => {
    render(<WorkflowCanvas />);
    await screen.findByTestId("rf-canvas");
    await waitFor(() => expect(lastFlow().colorMode).toBe("dark"));
    expect(screen.getByRole("button", { name: /Run workflow/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Workflow name")).toBeInTheDocument();
    // Interaction props untouched.
    expect(typeof lastFlow().onConnect).toBe("function");
    expect(lastMini().pannable).toBe(true);
    expect(lastMini().zoomable).toBe(true);
  });

  it("minimap and dot grid colours come from theme variables, not literal dark rgba", async () => {
    render(<WorkflowCanvas />);
    await screen.findByTestId("rf-canvas");
    expect(String(lastMini().maskColor)).toContain("var(--");
    const style = (lastMini().style ?? {}) as { backgroundColor?: string };
    expect(String(style.backgroundColor)).toContain("var(--");
    expect(String(lastBg().color)).toContain("var(--");
  });

  it("source: no hard-coded dark colorMode or dark rgba literals remain", () => {
    const src = read("components", "workflow", "WorkflowCanvas.tsx");
    expect(src).not.toContain('colorMode="dark"');
    expect(src).not.toContain("rgba(11,13,17");
    expect(src).not.toContain("rgba(7,8,9");
  });
});

/* ========================================================================== */
/*  F. No black slabs; a ratchet on the rest                                   */
/* ========================================================================== */

/** Every `bg-black/…` in a file, with the className string it sits in. An
 *  overlay (`fixed|absolute inset-0`) is allowed — a scrim is black in every
 *  theme — and so is an image/video letterbox, which none of these files has. */
function blackSlabs(src: string): string[] {
  const out: string[] = [];
  for (const m of src.matchAll(/bg-black\/[\w.[\]]+/g)) {
    const i = m.index ?? 0;
    const open = Math.max(src.lastIndexOf('"', i), src.lastIndexOf("`", i));
    const closeQ = src.indexOf('"', i);
    const closeB = src.indexOf("`", i);
    const close = [closeQ, closeB].filter((x) => x >= 0).reduce((a, b) => Math.min(a, b), src.length);
    const cls = src.slice(open + 1, close);
    if (/\binset-0\b/.test(cls)) continue;
    out.push(cls.trim().slice(0, 120));
  }
  return out;
}

describe("F. surfaces follow the theme (black is not remapped)", () => {
  it("the Build stage is a recessed THEME surface, not bg-black (a grey slab in Daylight)", () => {
    const src = read("app", "terminals", "page.tsx");
    const at = src.indexOf("ref={canvasRef}");
    expect(at).toBeGreaterThan(-1);
    const cls = src.slice(at, at + 400).match(/className="([^"]*)"/)?.[1] ?? "";
    expect(cls).not.toMatch(/bg-black/);
    expect(cls).toMatch(/\bbg-ink-\d{3}/);
  });

  it("self-dev's callout uses the shared notice-warn classes and no black chip", () => {
    const src = read("app", "self-dev", "page.tsx");
    expect(blackSlabs(src)).toEqual([]);
    expect(src).toMatch(/\bnotice-warn\b/);
    // The `self_dev_enabled` chip: the theme-safe pair, or the new code chip.
    expect(src).toMatch(/notice-warn-code|code-inline|<Code\b/);
    // Anti-vacuity: the words the user is told to look for are still there.
    expect(src).toContain("self_dev_enabled");
    expect(src).toContain("self_dev_root");
  });

  it("updates and webhooks: commands and URLs sit on a theme code chip, not bg-black", () => {
    for (const p of [["app", "updates", "page.tsx"], ["app", "webhooks", "page.tsx"]]) {
      const src = read(...p);
      expect(blackSlabs(src), p.join("/")).toEqual([]);
      expect(src, p.join("/")).toMatch(/code-inline|<Code\b/);
    }
    // Anti-vacuity: the copyable commands themselves are unchanged.
    const upd = read("app", "updates", "page.tsx");
    for (const cmd of ["git pull --ff-only", "uv sync", "pnpm build"]) expect(upd).toContain(cmd);
    expect(read("app", "webhooks", "page.tsx")).toContain("/webhooks/{slug.trim()}");
  });

  it("ratchet: `<code … bg-black/…>` chips across the dashboard only go down (22 before this wave)", () => {
    // Lower this ceiling as later waves migrate files; never raise it.
    // 6 after T2's own sweep (tools 5 + ConnectDoors 1, owned by T4/T3).
    const CODE_CHIP_MAX = 6;
    const hits = SOURCES.flatMap(({ path, text }) =>
      (text.match(/<code\b[^>]*\bbg-black\//g) ?? []).map(() => path),
    );
    expect(hits.length, hits.join("\n")).toBeLessThanOrEqual(CODE_CHIP_MAX);
  });
});

/* ========================================================================== */
/*  G. <Code>, .code-inline and <Button> (U1-3) + ratchets                     */
/* ========================================================================== */

type CodeT = ComponentType<{ children?: ReactNode; className?: string }>;
type Variant = "primary" | "secondary" | "soft" | "danger";
type ButtonT = ComponentType<
  {
    variant?: Variant;
    size?: "sm" | "md";
    children?: ReactNode;
  } & Record<string, unknown>
>;
const uiNew = ui as unknown as { Code?: CodeT; Button?: ButtonT };

function cssRuleBody(selector: string): string {
  const i = css.indexOf(`${selector} {`);
  if (i < 0) return "";
  return css.slice(i, css.indexOf("}", i));
}
function varRef(body: string, prop: RegExp): string | null {
  for (const line of body.split(/;|\n/)) {
    if (!prop.test(line)) continue;
    const m = line.match(/var\(--([a-z0-9-]+)\)/);
    if (m) return m[1];
  }
  return null;
}

describe("G. one code chip and one button, both theme-true", () => {
  it("<Code> renders a <code class='code-inline'> with its text", () => {
    expect(typeof uiNew.Code, "ui.tsx exports Code").toBe("function");
    if (!uiNew.Code) return;
    const C = uiNew.Code;
    render(<C>uv sync</C>);
    const el = screen.getByText("uv sync");
    expect(el.tagName).toBe("CODE");
    expect(el.className).toContain("code-inline");
    expect(el.className).not.toMatch(/bg-black/);
  });

  it(".code-inline is var-driven (ink background, zinc text) and reads in every theme", () => {
    const body = cssRuleBody(".code-inline");
    expect(body, ".code-inline rule in globals.css").not.toBe("");
    expect(body).not.toMatch(/\bblack\b|rgb\(0 0 0|rgba\(0,\s*0,\s*0/);
    const bgVar = varRef(body, /background/);
    const fgVar = varRef(body, /(^|\s)color\s*:/);
    expect(bgVar, "background: rgb(var(--ink-…))").toMatch(/^ink-\d{3}$/);
    expect(fgVar, "color: rgb(var(--zinc-…))").toMatch(/^zinc-\d{2,3}$/);
    if (!bgVar || !fgVar) return;
    for (const th of ["dark", "mark1", "mark8", "mark23", "mark29"] as const) {
      expect(
        contrast(THEME[th][fgVar], THEME[th][bgVar]),
        `${th} code text on code chip`,
      ).toBeGreaterThanOrEqual(AA);
    }
  });

  it("<Button variant size> maps onto the shared classes, defaults to type=button and passes props through", () => {
    expect(typeof uiNew.Button, "ui.tsx exports Button").toBe("function");
    if (!uiNew.Button) return;
    const B = uiNew.Button;
    const map: [Variant, string][] = [
      ["primary", "btn-accent"],
      ["secondary", "btn-ghost"],
      ["soft", "btn-soft"],
      ["danger", "btn-danger"],
    ];
    for (const [variant, cls] of map) {
      for (const [size, sizeCls] of [
        ["sm", "btn-sm"],
        ["md", "btn-md"],
      ] as const) {
        const onClick = vi.fn();
        render(
          <B variant={variant} size={size} onClick={onClick} aria-label={`${variant}-${size}`}>
            Go
          </B>,
        );
        const b = screen.getByRole("button", { name: `${variant}-${size}` });
        expect(b.className).toContain(cls);
        expect(b.className).toContain(sizeCls);
        expect(b.getAttribute("type")).toBe("button");
        fireEvent.click(b);
        expect(onClick).toHaveBeenCalledTimes(1);
        cleanup();
      }
    }
    const onClick = vi.fn();
    render(
      <B variant="primary" size="md" disabled onClick={onClick} type="submit" className="w-full">
        Save
      </B>,
    );
    const b = screen.getByRole("button", { name: "Save" });
    expect(b).toBeDisabled();
    expect(b.getAttribute("type")).toBe("submit");
    expect(b.className).toContain("w-full");
    fireEvent.click(b);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("globals.css defines the variants, the size steps AFTER the base buttons, and a visible keyboard focus", () => {
    for (const sel of [".btn-soft", ".btn-danger", ".btn-sm", ".btn-md"]) {
      expect(cssRuleBody(sel), `${sel} rule`).not.toBe("");
    }
    // Size steps must win over .btn-accent/.btn-ghost's own px-4 py-2 text-sm.
    const after = (a: string, b: string) => css.indexOf(`${a} {`) > css.indexOf(`${b} {`);
    expect(after(".btn-sm", ".btn-accent") && after(".btn-sm", ".btn-ghost")).toBe(true);
    expect(after(".btn-md", ".btn-accent") && after(".btn-md", ".btn-ghost")).toBe(true);
    // Danger reads through the tone token (deep in light).
    expect(cssRuleBody(".btn-danger")).toMatch(/tone-danger/);
    // Keyboard focus is visible on every button class.
    const selectors = [...css.matchAll(/([^{}]+)\{/g)].map((m) => m[1]);
    for (const cls of [".btn-accent", ".btn-ghost", ".btn-soft", ".btn-danger"]) {
      const bySelector = selectors.some((s) =>
        s.split(",").some((item) => item.includes(cls) && item.includes(":focus-visible")),
      );
      const byApply = /focus-visible:/.test(cssRuleBody(cls));
      expect(bySelector || byApply, `${cls} needs a :focus-visible style`).toBe(true);
    }
  });

  it("ratchet: ad-hoc button sizing, soft-button copies and half-pixel font sizes only go down", () => {
    // Green at introduction BY DESIGN — it stops the debt growing while pages
    // migrate to <Button>/the type scale in later waves. Lower, never raise.
    const BUTTON_OVERRIDES_MAX = 188;
    const SOFT_COPIES_MAX = 53;
    const HALF_PIXEL_MAX = 408;
    let btn = 0;
    let soft = 0;
    let half = 0;
    for (const { text } of SOURCES) {
      for (const m of text.matchAll(/(?:className=|`|")[^"`]*\bbtn-(?:accent|ghost)\b[^"`]*/g)) {
        if (/\b(py-|text-\[|text-xs\b|text-\d)/.test(m[0])) btn++;
      }
      soft += (text.match(/border-accent\/30 bg-accent\/\[0\.08\]/g) ?? []).length;
      half += (text.match(/\btext-\[(?:8|9|9\.5|10\.5|11\.5|12\.5|13\.5)px\]/g) ?? []).length;
    }
    expect(btn).toBeLessThanOrEqual(BUTTON_OVERRIDES_MAX);
    expect(soft).toBeLessThanOrEqual(SOFT_COPIES_MAX);
    expect(half).toBeLessThanOrEqual(HALF_PIXEL_MAX);
    // Anti-vacuity: the scan sees real buttons.
    expect(btn).toBeGreaterThan(50);
  });
});

/* ========================================================================== */
/*  H. Modal focus management                                                  */
/* ========================================================================== */

type ModalProps = Parameters<typeof Modal>[0] & { z?: number };
const ModalZ = Modal as unknown as (p: ModalProps) => ReactElement | null;

function Harness({ busy = false, onClosed }: { busy?: boolean; onClosed?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open dialog</button>
      <button>Elsewhere</button>
      {open && (
        <ModalZ
          label="Focus test"
          busy={busy}
          onClose={() => {
            onClosed?.();
            setOpen(false);
          }}
        >
          <button>First</button>
          <button disabled>Disabled</button>
          <button>Last</button>
          <button hidden>Hidden</button>
        </ModalZ>
      )}
    </>
  );
}

async function openHarness() {
  const opener = screen.getByRole("button", { name: "Open dialog" });
  opener.focus();
  fireEvent.click(opener);
  await screen.findByRole("dialog", { name: "Focus test" });
  return opener;
}

describe("H. a dialog takes focus, keeps it, and gives it back", () => {
  it("moves focus to the first focusable control when it opens", async () => {
    render(<Harness />);
    await openHarness();
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "First" })),
    );
  });

  it("traps Tab at the edges (disabled and hidden controls skipped) and prevents the native move", async () => {
    render(<Harness />);
    await openHarness();
    const first = screen.getByRole("button", { name: "First" });
    const last = screen.getByRole("button", { name: "Last" });
    await waitFor(() => expect(document.activeElement).toBe(first));
    last.focus();
    expect(fireEvent.keyDown(last, { key: "Tab" }), "Tab on the last is prevented").toBe(false);
    expect(document.activeElement).toBe(first);
    expect(
      fireEvent.keyDown(first, { key: "Tab", shiftKey: true }),
      "Shift+Tab on the first is prevented",
    ).toBe(false);
    expect(document.activeElement).toBe(last);
  });

  it("returns focus to the opener when it closes", async () => {
    render(<Harness />);
    const opener = await openHarness();
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "First" })),
    );
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });

  it("respects a control the dialog itself autofocuses", async () => {
    render(
      <ModalZ label="Autofocus" onClose={() => {}}>
        <button>Close</button>
        <input aria-label="Name" autoFocus />
      </ModalZ>,
    );
    const input = await screen.findByLabelText("Name");
    await waitFor(() => expect(document.activeElement).toBe(input));
  });

  it("a dialog with nothing focusable focuses the dialog box itself", async () => {
    render(
      <ModalZ label="Read only" onClose={() => {}}>
        <p>Just words.</p>
      </ModalZ>,
    );
    const dialog = await screen.findByRole("dialog", { name: "Read only" });
    await waitFor(() => expect(document.activeElement).toBe(dialog));
    expect(dialog.getAttribute("tabindex")).toBe("-1");
  });

  it("a stacked dialog keeps focus inside the TOP one and hands it back to its opener", async () => {
    function Stacked() {
      const [outer, setOuter] = useState(false);
      const [inner, setInner] = useState(false);
      return (
        <>
          <button onClick={() => setOuter(true)}>Open outer</button>
          {outer && (
            <ModalZ label="Outer" onClose={() => setOuter(false)}>
              <button>Outer first</button>
              <button onClick={() => setInner(true)}>Open inner</button>
              {inner && (
                <ModalZ label="Inner" onClose={() => setInner(false)} z={70}>
                  <button>Inner first</button>
                  <button onClick={() => setInner(false)}>Close inner</button>
                </ModalZ>
              )}
            </ModalZ>
          )}
        </>
      );
    }
    render(<Stacked />);
    fireEvent.click(screen.getByRole("button", { name: "Open outer" }));
    const openInner = await screen.findByRole("button", { name: "Open inner" });
    openInner.focus();
    fireEvent.click(openInner);
    const innerFirst = await screen.findByRole("button", { name: "Inner first" });
    await waitFor(() => expect(document.activeElement).toBe(innerFirst));
    const close = screen.getByRole("button", { name: "Close inner" });
    close.focus();
    fireEvent.keyDown(close, { key: "Tab" });
    expect(document.activeElement).toBe(innerFirst);
    fireEvent.click(close);
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Inner" })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(openInner));
    // The outer dialog is still open.
    expect(screen.getByRole("dialog", { name: "Outer" })).toBeInTheDocument();
  });

  it("takes a z prop for overlays that sat above the default layer (FilesPanel z-80, HUD z-100)", async () => {
    render(
      <ModalZ label="High" onClose={() => {}} z={80}>
        <button>Ok</button>
      </ModalZ>,
    );
    const overlay = (await screen.findByRole("dialog", { name: "High" })).parentElement as HTMLElement;
    expect(overlay.style.zIndex === "80" || overlay.className.includes("z-[80]")).toBe(true);
    cleanup();
    render(
      <ModalZ label="Default" onClose={() => {}}>
        <button>Ok</button>
      </ModalZ>,
    );
    const def = (await screen.findByRole("dialog", { name: "Default" })).parentElement as HTMLElement;
    // Anti-vacuity: the default layer is unchanged.
    expect(def.style.zIndex === "60" || def.className.includes("z-[60]")).toBe(true);
  });

  it("anti-vacuity: dismissal is exactly as before — Escape and backdrop close; busy freezes both", async () => {
    const closed = vi.fn();
    render(<Harness onClosed={closed} />);
    await openHarness();
    fireEvent.click(screen.getByRole("dialog").parentElement as HTMLElement);
    expect(closed).toHaveBeenCalledTimes(1);
    cleanup();

    const busyClosed = vi.fn();
    render(<Harness busy onClosed={busyClosed} />);
    await openHarness();
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByRole("dialog").parentElement as HTMLElement);
    expect(busyClosed).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    // A click inside the box never dismisses.
    fireEvent.click(screen.getByRole("button", { name: "First" }));
    expect(busyClosed).not.toHaveBeenCalled();
  });
});

/* ========================================================================== */
/*  I. Reduced motion                                                          */
/* ========================================================================== */

describe("I. framer motion honours the OS reduced-motion setting app-wide", () => {
  it("MotionProvider sets reducedMotion='user' for every m.* below it", () => {
    function Probe() {
      const cfg = useContext(MotionConfigContext);
      return <span data-testid="rm">{String(cfg.reducedMotion)}</span>;
    }
    render(
      <MotionProvider>
        <Probe />
        <p>child still renders</p>
      </MotionProvider>,
    );
    expect(screen.getByTestId("rm")).toHaveTextContent("user");
    expect(screen.getByText("child still renders")).toBeInTheDocument();
  });
});

/* ========================================================================== */
/*  J. Helper text: the 12px meta step, zinc-500 — paragraphs only             */
/* ========================================================================== */

/** The className of the tag that holds `needle` (the last class string within
 *  `window` chars before it). */
function classBefore(src: string, needle: string, window = 400): string {
  const at = src.indexOf(needle);
  if (at < 0) return `<<${needle} not found>>`;
  const head = src.slice(Math.max(0, at - window), at);
  const all = [...head.matchAll(/className="([^"]*)"/g)];
  return all.length ? all[all.length - 1][1] : "";
}

describe("J. hints that explain what to do are readable", () => {
  it("the type scale has a 12px `meta` step", () => {
    const fs = ((tailwindConfig.theme?.extend as Record<string, unknown> | undefined)?.fontSize ??
      {}) as Record<string, unknown>;
    const meta = fs.meta;
    const size = Array.isArray(meta) ? meta[0] : meta;
    expect(size).toBe("12px");
  });

  it("Build's rail: the empty-state paragraph is meta/zinc-500 and the pane count is not zinc-700", () => {
    const src = read("components", "terminal", "PaneRail.tsx");
    const para = classBefore(src, "No panes yet.");
    expect(para).toMatch(/\btext-(meta|xs|\[12px\])\b/);
    expect(para).toContain("text-zinc-500");
    expect(para).not.toMatch(/text-zinc-600|text-\[11\.5px\]/);
    const count = classBefore(src, "{panes.length}</span>", 120);
    expect(count).not.toContain("text-zinc-700");
    expect(count).toMatch(/text-zinc-(400|500)/);
  });

  it("Computer use: 'No domains allowed yet.' and 'No trace recorded…' read in zinc-500", () => {
    const src = read("app", "computeruse", "page.tsx");
    for (const needle of ["No domains allowed yet.", "No trace recorded for this run."]) {
      const cls = classBefore(src, needle, 160);
      expect(cls, needle).toContain("text-zinc-500");
      expect(cls, needle).not.toContain("text-zinc-600");
    }
  });
});

/* ========================================================================== */
/*  K. Dark brand marks invert with the theme                                  */
/* ========================================================================== */

describe("K. a dark brand mark is drawn in currentColor so it shows on light tiles", () => {
  it("Anthropic and GitHub use fill=currentColor with a zinc text class on the <svg>", () => {
    for (const id of ["anthropic", "github"]) {
      const { container } = render(<ProviderMark id={id} fallback={null} />);
      const svg = container.querySelector("svg");
      expect(svg, id).not.toBeNull();
      expect(svg?.getAttribute("fill"), id).toBe("currentColor");
      expect(svg?.getAttribute("class") ?? "", id).toMatch(/\btext-zinc-\d{2,3}\b/);
      expect(svg?.getAttribute("aria-label") ?? "", id).toMatch(/ logo$/);
      cleanup();
    }
  });

  it("anti-vacuity: a bright brand colour keeps its own hex", () => {
    const { container } = render(<ProviderMark id="google_drive" fallback={null} />);
    expect(container.querySelector("svg")?.getAttribute("fill") ?? "").toMatch(/^#[0-9a-f]{6}$/i);
  });
});
