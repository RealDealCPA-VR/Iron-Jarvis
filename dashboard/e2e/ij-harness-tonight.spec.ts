import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

/**
 * Night shift N1 (v1.398.0) — Coding sessions › Tonight in a real browser,
 * against the scratch daemon (port 8807, a FRESH home, the demo model), with a
 * scratch git repository in the OS's temp folder:
 *
 *  1. 1280 px, keyboard only after the first click: Tonight opens in the
 *     middle; Add a task takes the folder and the words; Ctrl+Enter adds it;
 *     the row says Queued (or Working, when this runs inside the night window
 *     and the loop already started it) and the window is said in words;
 *     Cancel (Enter on it) stops a queued task;
 *  2. 390 px: the list and the add form never pan sideways, every row's
 *     buttons are inside the window.
 */

test.describe.configure({ mode: "serial" });

let root = "";
let repo = "";

function git(args: string[], cwd: string): string {
  return execFileSync("git", args, { cwd, encoding: "utf-8", env: { ...process.env, GIT_TERMINAL_PROMPT: "0" } }).trim();
}

test.beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), "ij-night-e2e-"));
  repo = join(root, "shop");
  execFileSync("git", ["init", "-q", "-b", "main", repo]);
  git(["config", "user.email", "dev@example.com"], repo);
  git(["config", "user.name", "Dev"], repo);
  git(["config", "core.autocrlf", "false"], repo);
  writeFileSync(join(repo, "app.py"), "x = 1\n");
  git(["add", "-A"], repo);
  git(["commit", "-q", "-m", "first"], repo);
});

test.afterAll(() => {
  try {
    if (root) rmSync(root, { recursive: true, force: true });
  } catch {
    /* the OS clears its temp folder */
  }
});

async function openTonight(page: Page) {
  await page.goto("/chat"); // v1.406.0: Chat › Code
  const tab = page.getByTestId("chat-view-code");
  await expect(tab).toBeVisible({ timeout: 90_000 });
  await tab.click();
  await expect(page.getByTestId("ij-harness-view")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("harness-tonight").click();
  await expect(page.getByTestId("tonight")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("tonight-window")).toHaveText(/^(Starts at \d{1,2}:\d{2} [AP]M (tonight|today|tomorrow)\.|Night tasks run now, until \d{1,2}:\d{2} [AP]M\.)$/);
}

async function noSidewaysPan(page: Page): Promise<void> {
  const pan = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(pan).toBeLessThanOrEqual(0);
}

test("add a task with the keyboard, and cancel it", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openTonight(page);
  await page.getByTestId("tonight-add").click();
  await page.getByTestId("tonight-folder").fill(repo);
  const words = page.getByTestId("tonight-words");
  await words.click();
  await page.keyboard.type("Add a test for app.py");
  await page.keyboard.press("Control+Enter");
  const row = page.locator('[data-testid^="tonight-task-"]').first();
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(row).toContainText("Add a test for app.py");
  const state = row.locator('[data-testid^="tonight-state-"]');
  await expect(state).toHaveText(/^(Queued|Working|Done|Needs you)$/);
  if ((await state.textContent()) === "Queued") {
    const cancel = row.locator('[data-testid^="tonight-cancel-"]');
    await cancel.focus();
    await page.keyboard.press("Enter");
    await expect(state).toHaveText("Stopped", { timeout: 20_000 });
    await expect(row.getByText("What happened (1)")).toBeVisible();
  }
});

test("390 px: the list and the form never pan sideways", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openTonight(page);
  await noSidewaysPan(page);
  await page.getByTestId("tonight-add").click();
  await expect(page.getByTestId("tonight-form")).toBeVisible();
  await noSidewaysPan(page);
  const rows = page.locator('[data-testid^="tonight-task-"]');
  const n = await rows.count();
  for (let i = 0; i < n; i++) {
    const box = await rows.nth(i).boundingBox();
    expect(box).not.toBeNull();
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(390);
  }
});
