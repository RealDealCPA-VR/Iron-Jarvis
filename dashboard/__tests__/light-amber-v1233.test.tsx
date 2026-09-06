/**
 * Amber on the light Marks (v1.233.0). Reported after v1.232.1: "on light
 * mode the popups and certain boxes / text that appear in yellow are
 * impossible to see". The remap lives in globals.css as one generated block;
 * this test keeps it complete: any amber/orange/yellow TEXT, BORDER or RING
 * utility used anywhere in the dashboard must have a Mark 1 + Mark 8 rule.
 * (Source is read with line endings normalised — the CI runner checks out
 * with CRLF.)
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const read = (p: string) => readFileSync(p, "utf8").replace(/\r\n/g, "\n");

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (name === "__tests__" || name === "node_modules" || name === ".next") continue;
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(tsx?|jsx?)$/.test(name)) out.push(p);
  }
  return out;
}

const cwd = process.cwd();
const css = read(join(cwd, "app", "globals.css"));
const start = css.indexOf("/* light-amber-overrides:start */");
const end = css.indexOf("/* light-amber-overrides:end */");
const block = css.slice(start, end);

const used = new Set<string>();
for (const dir of ["app", "components", "lib"]) {
  for (const f of walk(join(cwd, dir))) {
    for (const m of read(f).matchAll(/\b(text|border|ring)-(amber|orange|yellow)-\d{3}(?:\/\d{1,3})?\b/g)) {
      used.add(m[0]);
    }
  }
}

describe("amber utilities read on the light Marks", () => {
  it("has the generated block", () => {
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
  });

  it("covers every amber/orange/yellow text, border and ring utility in use", () => {
    const missing = [...used].filter((c) => {
      const esc = "." + c.replace("/", "\\/");
      return !block.includes(':root[data-theme="mark1"] ' + esc) || !block.includes(':root[data-theme="mark8"] ' + esc);
    });
    expect(missing, "add these to the light-amber block in globals.css").toEqual([]);
    expect(used.size).toBeGreaterThan(20);
  });

  it("maps pale text to deep amber ink, not another pale tint", () => {
    // text-amber-300 is the most common offender (95 sites): deep amber-800.
    expect(block).toMatch(/\.text-amber-300 \{ color: rgb\(146 64 14 \/ 1\); \}/);
    // opacity variants keep strong ink (never below 0.8 on a light canvas).
    for (const m of block.matchAll(/\.text-[a-z]+-\d{3}\\\/\d+ \{ color: rgb\([^)]*\/ ([0-9.]+)\)/g)) {
      expect(Number(m[1])).toBeGreaterThanOrEqual(0.8);
    }
  });

  it("leaves the dark Marks alone (no unscoped amber overrides)", () => {
    for (const line of block.split("\n")) {
      if (line.includes("{")) expect(line.startsWith(':root[data-theme="mark')).toBe(true);
    }
  });
});
