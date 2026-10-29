import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

/**
 * v1.406.0 part 1 + 5 — Chat | Code, in a real browser against the scratch
 * daemon (a FRESH home, the demo model):
 *
 *  - the chat page's switch is centred in its column and does NOT move when
 *    the view changes (the v1.370.2 rule, in pixels), at 1280 × 800 and on a
 *    390 × 844 phone, where nothing pans sideways in either view;
 *  - an OLD link (`/terminals?session=<id>`) lands in Code with that session;
 *  - Build has no switch;
 *  - the sidebar holds the right list: Chats in Chat, the coding sessions in
 *    Code (at 1280 px, where the sidebar is on screen);
 *  - the sidebar's items are Chat, Build, Projects, Everything, Settings.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;

test.describe.configure({ mode: "serial" });

let work = "";
let sessionId = "";

test.beforeAll(async ({ request }) => {
  work = mkdtempSync(join(tmpdir(), "ij-chat-code-e2e-"));
  const res = await request.post(`${DAEMON}/build/sessions`, { data: { folder: work } });
  expect(res.ok()).toBe(true);
  const body = (await res.json()) as { id?: string };
  sessionId = body.id ?? "";
  expect(sessionId).not.toBe("");
});

test.afterAll(() => {
  try {
    if (work) rmSync(work, { recursive: true, force: true });
  } catch {
    /* left for the OS */
  }
});

async function switchRect(page: Page) {
  return page.evaluate(() => {
    const r = (id: string) => document.querySelector(`[data-testid="${id}"]`)!.getBoundingClientRect();
    const sw = r("chat-view-switch");
    const row = r("chat-view-switch-row");
    return {
      centre: sw.left + sw.width / 2,
      rowCentre: row.left + row.width / 2,
      top: sw.top,
      left: sw.left,
      right: sw.right,
      pan: document.documentElement.scrollWidth - window.innerWidth,
    };
  });
}

for (const size of [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  test.describe(`${size.width} px — the Chat | Code switch is centred and fixed`, () => {
    test.use({ viewport: size });
    test("same place in both views, in the middle, nothing pans", async ({ page }) => {
      await page.goto("/chat");
      await expect(page.getByTestId("chat-view-chat")).toBeVisible({ timeout: 90_000 });
      await expect(page.getByTestId("chat-card")).toBeVisible({ timeout: 60_000 });
      await page.waitForTimeout(500);
      const chat = await switchRect(page);
      await page.getByTestId("chat-view-code").click();
      await expect(page.getByTestId("ij-harness-view")).toBeVisible({ timeout: 60_000 });
      await expect(page).toHaveURL(/view=code/);
      await page.waitForTimeout(500);
      const code = await switchRect(page);
      // Centred in its column.
      expect(Math.abs(chat.centre - chat.rowCentre)).toBeLessThanOrEqual(1.5);
      expect(Math.abs(code.centre - code.rowCentre)).toBeLessThanOrEqual(1.5);
      // Fixed: the same place in both views, across and down.
      expect(Math.abs(chat.centre - code.centre)).toBeLessThanOrEqual(1);
      expect(Math.abs(chat.top - code.top)).toBeLessThanOrEqual(1);
      // Inside the window, and no sideways pan in either view.
      for (const v of [chat, code]) {
        expect(v.left).toBeGreaterThanOrEqual(0);
        expect(v.right).toBeLessThanOrEqual(size.width);
        expect(v.pan).toBeLessThanOrEqual(0);
      }
      // And back: the chat is where it was.
      await page.getByTestId("chat-view-chat").click();
      await expect(page.getByTestId("chat-card")).toBeVisible();
      await expect(page.getByTestId("ij-harness-view")).toHaveCount(0);
    });
  });
}

test("an old /terminals?session= link lands in Code, on that session", async ({ page }) => {
  await page.goto(`/terminals?session=${sessionId}`);
  await expect(page).toHaveURL(new RegExp(`/chat\\?view=code&session=${sessionId}`), { timeout: 90_000 });
  await expect(page.getByTestId("harness-session")).toHaveAttribute("data-session", sessionId, { timeout: 90_000 });
  await expect(page.getByTestId("chat-view-code")).toHaveAttribute("aria-selected", "true");
});

test("Build is the terminals only: no switch", async ({ page }) => {
  await page.goto("/terminals");
  await expect(page.getByTestId("header-new-terminal")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId("build-view-switch")).toHaveCount(0);
  await expect(page.getByTestId("ij-harness-view")).toHaveCount(0);
});

test.describe("1280 px — the sidebar shows the right list per view", () => {
  test.use({ viewport: { width: 1280, height: 800 } });
  test("Chats in Chat, the coding sessions in Code; the five items", async ({ page }) => {
    await page.goto("/chat");
    const side = page.getByTestId("app-sidebar");
    await expect(side.getByTestId("sidebar-list-heading")).toHaveText(/Chats/, { timeout: 90_000 });
    await expect(side.getByTestId("chat-thread-rail")).toBeVisible({ timeout: 60_000 });
    const items = side.getByTestId("sidebar-nav").locator('a[data-testid^="sidebar-nav-"]:not([data-pinned])');
    await expect(items).toHaveText(["Chat", "Build", "Projects", "Everything", "Settings"]);
    await page.getByTestId("chat-view-code").click();
    await expect(side.getByTestId("sidebar-list-heading")).toHaveText(/Coding sessions/);
    await expect(side.getByTestId("harness-sidebar")).toBeVisible({ timeout: 60_000 });
    await expect(side.getByTestId("chat-thread-rail")).toHaveCount(0);
    // The session list is in the sidebar, not in the page as well.
    await expect(page.getByTestId("harness-sidebar")).toHaveCount(1);
    await page.getByTestId("chat-view-chat").click();
    await expect(side.getByTestId("chat-thread-rail")).toBeVisible();
    await expect(page.getByTestId("harness-sidebar")).toHaveCount(0);
  });
});
