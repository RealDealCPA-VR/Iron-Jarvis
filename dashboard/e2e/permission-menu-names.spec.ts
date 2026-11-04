import { expect, test, type Page } from "@playwright/test";

/**
 * v1.430.0, in a real browser: the permission menu shows each level's NAME
 * only (one line each), and a level's meaning shows in a box beside its row
 * after the pointer rests there — inside the window on a desktop (beside the
 * menu, never over it) and on a phone (under or over the row). jsdom cannot
 * measure any of this; the unit pins are in
 * __tests__/permission-menu-names-v1430.test.tsx.
 */

const SIZES = [
  { width: 1280, height: 800 },
  { width: 1024, height: 640 },
  { width: 390, height: 844 },
] as const;

const NAMES = ["Ask before every action", "Ask before commands", "Accept edits", "Auto", "Don't ask"];

async function openMenu(page: Page) {
  await page.goto("/chat");
  const chip = page.locator("#chat-approval-mode");
  await expect(chip).toBeVisible();
  await chip.click();
  const menu = page.getByTestId("permission-menu");
  await expect(menu).toBeVisible();
  return menu;
}

for (const size of SIZES) {
  test(`names only, and the meaning beside the row at ${size.width}x${size.height}`, async ({ page }) => {
    await page.setViewportSize(size);
    const menu = await openMenu(page);
    const rows = menu.getByTestId("permission-item");
    await expect(rows).toHaveText(NAMES);
    // One line each: no row is taller than a single line of words.
    for (let i = 0; i < NAMES.length; i++) {
      const box = await rows.nth(i).boundingBox();
      expect(box, NAMES[i]).not.toBeNull();
      expect(box!.height, `${NAMES[i]} wraps`).toBeLessThan(40);
    }
    const m = (await menu.boundingBox())!;
    expect(m.x).toBeGreaterThanOrEqual(0);
    expect(m.y).toBeGreaterThanOrEqual(0);
    expect(m.x + m.width).toBeLessThanOrEqual(size.width);
    expect(m.y + m.height).toBeLessThanOrEqual(size.height);

    const auto = rows.filter({ hasText: /^Auto$/ });
    const a = (await auto.boundingBox())!;
    await page.mouse.move(a.x + 20, a.y + a.height / 2);
    // Not at once…
    await page.waitForTimeout(150);
    await expect(page.getByTestId("permission-hint")).toHaveCount(0);
    // …after the pause.
    const hint = page.getByTestId("permission-hint");
    await expect(hint).toBeVisible({ timeout: 3_000 });
    await expect(hint).toHaveAttribute("data-mode", "auto");
    await expect(hint).toContainText("its own safety check");
    const h = (await hint.boundingBox())!;
    expect(h.x, "hint left edge").toBeGreaterThanOrEqual(7.5);
    expect(h.y, "hint top edge").toBeGreaterThanOrEqual(7.5);
    expect(h.x + h.width, "hint right edge").toBeLessThanOrEqual(size.width - 7.5);
    expect(h.y + h.height, "hint bottom edge").toBeLessThanOrEqual(size.height - 7.5);
    const side = await hint.getAttribute("data-side");
    if (size.width >= 1024) {
      // Beside the menu, level with its row, never covering the names.
      expect(side === "right" || side === "left").toBe(true);
      const besideRight = h.x >= m.x + m.width;
      const besideLeft = h.x + h.width <= m.x;
      expect(besideRight || besideLeft, "the box covers the menu").toBe(true);
      expect(Math.abs(h.y - a.y), "the box is level with its row").toBeLessThan(2);
    } else {
      expect(side).toBe("under");
    }
    // The next row's meaning shows at once.
    const yolo = rows.filter({ hasText: /^Don't ask$/ });
    const y = (await yolo.boundingBox())!;
    await page.mouse.move(y.x + 20, y.y + y.height / 2);
    await expect(hint).toHaveAttribute("data-mode", "yolo", { timeout: 500 });
    // Leaving the menu hides it; a click still picks.
    await page.mouse.move(Math.max(2, m.x - 40), Math.max(2, m.y - 40));
    await expect(page.getByTestId("permission-hint")).toHaveCount(0);
    await auto.click();
    await expect(menu).toHaveCount(0);
    await expect(page.locator("#chat-approval-mode")).toHaveAttribute("data-mode", "auto");
  });
}
