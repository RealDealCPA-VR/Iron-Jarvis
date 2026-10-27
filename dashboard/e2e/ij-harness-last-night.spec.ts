import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

/**
 * Night shift N2 (v1.399.0) — Coding sessions › Last night in a real browser.
 *
 * The scratch daemon (port 8807, a FRESH home) has run no night, so the one
 * summary read is answered from the dashboard's own contract fixture
 * (`__tests__/fixtures/last-night-v1399.json`, the daemon's keys — held equal
 * by tests/test_night_morning_run_v1399.py), and the git presses are answered
 * here too (nothing reaches a repository):
 *
 *  1. `/terminals?night=<date>` (the bell's row, the tray notice) opens
 *     Coding sessions on Last night; failed and needs-you first; the main
 *     folder change said open;
 *  2. keyboard only: Throw away takes TWO presses of Enter, and only the
 *     second asks Iron Jarvis to move the folder;
 *  3. 390 px: nothing pans sideways, every row and every button is inside the
 *     window.
 */

test.describe.configure({ mode: "serial" });

const FIXTURE = JSON.parse(
  readFileSync(join(__dirname, "..", "__tests__", "fixtures", "last-night-v1399.json"), "utf-8").replace(/\r\n/g, "\n"),
);

async function answerFromFixture(page: Page, discards: string[]) {
  await page.route("**/build/night/summaries", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(FIXTURE) }),
  );
  await page.route("**/build/sessions/*/git/discard", async (route) => {
    discards.push(route.request().postData() ?? "");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ moved: true, note: "The worktree is in Iron Jarvis's trash and the branch ij/fix-the-parser-77aa is kept." }),
    });
  });
}

async function openLastNight(page: Page) {
  await page.goto("/terminals?night=2026-10-26");
  await expect(page.getByTestId("ij-harness-view")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId("last-night")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("harness-last-night")).toHaveAttribute("aria-current", "page");
}

async function noSidewaysPan(page: Page): Promise<void> {
  const pan = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(pan).toBeLessThanOrEqual(0);
}

test("a ?night= link opens Last night; failed and needs-you first; Throw away is two presses", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const discards: string[] = [];
  await answerFromFixture(page, discards);
  await openLastNight(page);
  await expect(page.getByTestId("last-night-counts")).toHaveText("3 tasks: 1 failed, 1 needs you, 1 done.");
  const states = await page.locator('[data-testid="last-night-rows-2026-10-26"] > li[data-state]').evaluateAll(
    (els) => els.map((e) => (e as HTMLElement).dataset.state),
  );
  expect(states).toEqual(["failed", "needs_you", "done"]);
  await expect(page.getByTestId("last-night-main-changed-night_needs")).toBeVisible();
  // Keyboard only: focus Throw away, Enter arms it, Enter again moves the folder.
  const throwAway = page.getByTestId("last-night-throw-night_fail").getByRole("button");
  await throwAway.focus();
  await page.keyboard.press("Enter");
  await expect(throwAway).toHaveText("Move its folder to the trash?");
  expect(discards).toEqual([]);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("last-night-said-night_fail")).toContainText("the branch ij/fix-the-parser-77aa is kept");
  expect(discards).toEqual([JSON.stringify({ confirm: true })]);
});

test("390 px: Last night never pans sideways, and every button is inside the window", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await answerFromFixture(page, []);
  await openLastNight(page);
  await noSidewaysPan(page);
  const rows = page.locator('[data-testid^="last-night-task-"]');
  const n = await rows.count();
  expect(n).toBeGreaterThanOrEqual(3);
  for (let i = 0; i < n; i++) {
    const row = rows.nth(i);
    if (!(await row.isVisible())) continue; // an older night, folded
    const box = await row.boundingBox();
    expect(box).not.toBeNull();
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(390);
    const buttons = row.getByRole("button");
    const m = await buttons.count();
    for (let j = 0; j < m; j++) {
      const b = await buttons.nth(j).boundingBox();
      expect(b).not.toBeNull();
      expect((b?.x ?? 0) + (b?.width ?? 0)).toBeLessThanOrEqual(390);
      expect(b?.x ?? -1).toBeGreaterThanOrEqual(0);
    }
  }
  // An older night opens with the keyboard, and stays inside the window.
  const older = page.getByTestId("last-night-older-2026-10-25").locator(":scope > summary");
  await older.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("last-night-task-night_old")).toBeVisible();
  await noSidewaysPan(page);
});
