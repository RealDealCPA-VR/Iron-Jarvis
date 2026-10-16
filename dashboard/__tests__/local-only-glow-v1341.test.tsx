/**
 * v1.341.0: the "Local only" chip GLOWS when it is on.
 *
 * The user's request: "when the local button is selected that it slightly
 * glows no matter the theme so those with color blindness issues can tell."
 * Before, the on-state was a green label on a dark chip — a hue change only.
 * Now the on-state carries a 1px ring and a soft halo in the theme's success
 * token: a brightness change around the chip that does not depend on telling
 * green from grey, and a token every Mark and every made palette defines.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { composerChipClass } from "@/lib/composerChips";

const ROOT = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

describe("the Local only chip glows when on", () => {
  it("the on-state carries a ring and a halo drawn from the success token", () => {
    const on = composerChipClass(true, "success");
    const shadow = on.split(/\s+/).find((c) => c.startsWith("shadow-["));
    expect(shadow, "a box-shadow glow").toBeDefined();
    // A ring (zero blur, 1px spread) AND a halo (a blurred second shadow).
    expect(shadow).toMatch(/0_0_0_1px_rgb\(var\(--tone-success\)\/0\.\d+\)/);
    expect(shadow).toMatch(/,0_0_\d+px_rgb\(var\(--tone-success\)\/0\.\d+\)/);
    // The words carry weight too — a second non-hue cue.
    expect(on).toContain("font-medium");
  });

  it("the glow is only on the on-state of the success chip", () => {
    expect(composerChipClass(false, "success")).not.toMatch(/shadow-\[/);
    expect(composerChipClass(false)).not.toMatch(/shadow-\[/);
    // The accent chips (project, tools) stay flat: one glowing chip means one thing.
    expect(composerChipClass(true)).not.toMatch(/shadow-\[/);
  });

  it("every theme defines the token the glow reads, so it shows no matter the theme", () => {
    const css = read("app/globals.css");
    // The dark root and every light Mark re-ink the tone; a palette the user
    // makes sets it from the generator's table.
    expect(css).toMatch(/:root\s*\{[^}]*--tone-success:/);
    expect((css.match(/--tone-success:/g) ?? []).length).toBeGreaterThanOrEqual(3);
    const palette = read("lib/themePalette.ts");
    expect(palette).toContain('"tone-success":');
  });
});
