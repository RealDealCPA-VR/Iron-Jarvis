import { expect, test, type Page } from "@playwright/test";
import { SIDEBAR_HREFS, SURFACES, type Surface } from "../lib/surfaces";
import { RELOCATED } from "../lib/relocated";

/**
 * The brief's browser acceptance tests (calm UI redesign S12):
 *
 *  T1 Home minimalism — on `/`: at most 4 sidebar items, no dashboard, stat
 *     tiles or module grid, and the composer has focus on load.
 *  T2 Reachability — every surface in the manifest (lib/surfaces.ts) is
 *     reached through the command palette AND within 2 clicks from home.
 *  T3 Fresh profile — an empty home opens to chat with no blocking modal and
 *     a message gets an answer from the mocked model.
 *  390 px — home, Settings and Everything never pan sideways.
 *
 * Evidence screenshots (home, Settings, palette at 1440 and 390 px) are
 * written when IJ_E2E_SHOTS names a folder.
 */

const composer = (page: Page) => page.getByRole("textbox", { name: "Message" });
const paletteBox = (page: Page) => page.getByRole("combobox", { name: "Search pages, skills, chats and projects" });

/** Where a surface lands: relocated routes redirect to their new home. */
function landing(href: string): string {
  return RELOCATED[href]?.base ?? href;
}

async function expectLanded(page: Page, s: Surface) {
  const want = landing(s.href);
  await page.waitForURL((u) => u.pathname === want, { timeout: 60_000 });
  await expect(page.getByText("This page could not be found")).toHaveCount(0);
}

/** The palette is loaded after hydration (components/Overlays.tsx); on a
 *  cold dev route the first press can land before it listens — press the
 *  title bar's Search until the box is there. */
async function openPalette(page: Page) {
  const box = paletteBox(page);
  for (let i = 0; i < 15; i++) {
    await page.getByRole("button", { name: /Search Iron Jarvis/ }).click();
    if (await box.isVisible().catch(() => false)) return box;
    await page.waitForTimeout(1000);
    if (await box.isVisible().catch(() => false)) return box;
  }
  await expect(box).toBeVisible();
  return box;
}

async function openHome(page: Page) {
  await page.goto("/");
  await expect(composer(page)).toBeVisible({ timeout: 90_000 });
}

test.describe.configure({ mode: "serial" });

test("T3 — a fresh profile opens to chat with no blocking modal, and the mock model answers", async ({ page }) => {
  await openHome(page);
  // No blocking first-run screen (the wizard is retired, AUDIT Q7).
  await expect(page.locator('[role="dialog"][aria-modal="true"]')).toHaveCount(0);
  await composer(page).fill("Hello — are you there?");
  await composer(page).press("Enter");
  await expect(page.getByText("Hello — are you there?").first()).toBeVisible();
  // The offline mock's scripted answer: a real round trip through the daemon.
  await expect(page.getByText(/Done\./).first()).toBeVisible({ timeout: 90_000 });
});

test("T1 — home is a chat: ≤ 4 sidebar items, no dashboard or grid, the composer focused", async ({ page }) => {
  await page.goto("/?new=1");
  const box = composer(page);
  await expect(box).toBeVisible({ timeout: 90_000 });
  await expect(box).toBeFocused();
  const items = page.getByTestId("sidebar-nav").locator('a[data-testid^="sidebar-nav-"]:not([data-pinned])');
  expect(await items.count()).toBeLessThanOrEqual(4);
  await expect(items).toHaveText(["Build", "Projects", "Everything", "Settings"]);
  await expect(page.getByTestId("app-desk")).toHaveCount(0);
  await expect(page.locator('[data-testid^="group-"]')).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Overview" })).toHaveCount(0);
  await expect(page.getByText("Problems today")).toHaveCount(0);
});

test.describe("T2 — every surface: the palette, and ≤ 2 clicks from home", () => {
  for (const s of SURFACES) {
    test(`${s.label} (${s.href})`, async ({ page }) => {
      // Through the command palette.
      await openHome(page);
      const box = await openPalette(page);
      await box.fill(s.label);
      const row = page.locator(`[role="option"][data-href="${s.href}"]`).first();
      await expect(row).toBeVisible();
      await row.click();
      await expectLanded(page, s);

      // Within two clicks from home.
      await openHome(page);
      let clicks = 0;
      if (s.href === "/" || s.href === "/chat") {
        // The home IS the chat surface: zero clicks.
      } else if ((SIDEBAR_HREFS as readonly string[]).includes(s.href)) {
        await page.getByTestId(`sidebar-nav-${s.href.slice(1)}`).click();
        clicks = 1;
      } else {
        await page.getByTestId("sidebar-nav-everything").click();
        await page.getByTestId(`everything-link-${s.href.slice(1)}`).click();
        clicks = 2;
      }
      expect(clicks).toBeLessThanOrEqual(2);
      if (clicks > 0) await expectLanded(page, s);
    });
  }
});

test.describe("390 px — nothing pans sideways", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  for (const path of ["/", "/settings", "/everything"]) {
    test(`${path} fits the width`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(800);
      const pan = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(pan).toBeLessThanOrEqual(0);
    });
  }
});

test("evidence screenshots (when IJ_E2E_SHOTS names a folder)", async ({ page }) => {
  const dir = process.env.IJ_E2E_SHOTS;
  test.skip(!dir, "set IJ_E2E_SHOTS to write the screenshots");
  for (const [w, h, tag] of [
    [1440, 900, "desktop"],
    [390, 844, "phone"],
  ] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.goto("/?new=1");
    await expect(composer(page)).toBeVisible({ timeout: 90_000 });
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${dir}/home-${tag}.png` });
    await page.goto("/settings");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${dir}/settings-${tag}.png` });
    await page.goto("/?new=1");
    await expect(composer(page)).toBeVisible({ timeout: 90_000 });
    await (await openPalette(page)).fill("notifications");
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${dir}/palette-${tag}.png` });
  }
});
