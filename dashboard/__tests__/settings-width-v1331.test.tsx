/**
 * Settings cards sat in a 720px column with a wide empty band to their
 * right on a desktop window. v1.331.0 widens the column by 30% (936px); the
 * group nav and the phone layout are unchanged.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const src = readFileSync(join(__dirname, "..", "components", "settings", "SettingsHome.tsx"), "utf8").replace(/\r\n/g, "\n");

describe("Settings column width (v1.331.0)", () => {
  it("the group column is 936px wide at most (720px + 30%)", () => {
    const i = src.indexOf('data-testid="settings-group"');
    expect(i).toBeGreaterThan(-1);
    const block = src.slice(Math.max(0, i - 400), i);
    expect(block).toContain("max-w-[936px]");
    expect(block).not.toContain("max-w-[720px]");
  });

  it("the nav keeps its 13rem rail beside it", () => {
    expect(src).toContain("md:grid-cols-[13rem_minmax(0,1fr)]");
  });
});
