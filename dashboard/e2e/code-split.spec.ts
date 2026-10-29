import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

/**
 * v1.406.0 part 7 — two coding sessions side by side, in a real browser
 * against the scratch daemon (a FRESH home, the demo model):
 *
 *  - 1600 × 900 and 1280 × 800: a row's "Open beside" opens a second column;
 *    both columns and the shared pane column sit inside the window and
 *    nothing pans sideways; a message sent in EACH column gets its own
 *    reply; the address carries `&beside=` and a reload keeps both; × returns
 *    to one column;
 *  - 390 × 844: the same address shows ONE column with a tab per session,
 *    the tabs switch, nothing pans sideways.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;

test.describe.configure({ mode: "serial" });

let work = "";
let first = "";
let second = "";

test.beforeAll(async ({ request }) => {
  work = mkdtempSync(join(tmpdir(), "ij-code-split-e2e-"));
  for (const slot of ["first", "second"] as const) {
    const res = await request.post(`${DAEMON}/build/sessions`, { data: { folder: work } });
    expect(res.ok()).toBe(true);
    const id = ((await res.json()) as { id?: string }).id ?? "";
    expect(id).not.toBe("");
    if (slot === "first") first = id;
    else second = id;
  }
});

test.afterAll(() => {
  try {
    if (work) rmSync(work, { recursive: true, force: true });
  } catch {
    /* left for the OS */
  }
});

async function layout(page: Page) {
  return page.evaluate(() => {
    const box = (id: string) => {
      const el = document.querySelector(`[data-testid="${id}"]`);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { left: r.left, right: r.right, width: r.width, height: r.height };
    };
    return {
      left: box("harness-column-left"),
      right: box("harness-column-right"),
      panes: box("harness-split-panes"),
      pan: document.documentElement.scrollWidth - window.innerWidth,
      width: window.innerWidth,
    };
  });
}

async function send(page: Page, side: "left" | "right", words: string) {
  const col = page.getByTestId(`harness-column-${side}`);
  const box = col.getByTestId("harness-input");
  await box.click();
  await box.fill(words);
  await box.press("Enter");
}

for (const size of [
  { width: 1600, height: 900 },
  { width: 1280, height: 800 },
]) {
  test.describe(`${size.width} px — two columns side by side`, () => {
    test.use({ viewport: size });
    test("open beside, both reply, the address keeps it, × returns to one, nothing pans", async ({ page }) => {
      await page.goto(`/chat?view=code&session=${first}`);
      await expect(page.getByTestId("harness-session").first()).toHaveAttribute("data-session", first, {
        timeout: 90_000,
      });
      const row = page.getByTestId(`harness-session-row-${second}`);
      await row.hover();
      await page.getByTestId(`harness-open-beside-${second}`).click();
      await expect(page.getByTestId("harness-columns")).toHaveAttribute("data-split", "columns", { timeout: 30_000 });
      await expect(page).toHaveURL(new RegExp(`session=${first}.*beside=${second}`));
      await expect(page.getByTestId("harness-column-right")).toHaveAttribute("data-focused", "true");
      await expect(page.getByTestId("harness-split-panes").getByTestId("harness-panes")).toBeVisible();
      await page.waitForTimeout(400);
      const two = await layout(page);
      expect(two.pan).toBeLessThanOrEqual(0);
      for (const b of [two.left, two.right, two.panes]) {
        expect(b).not.toBeNull();
        expect(b!.left).toBeGreaterThanOrEqual(0);
        expect(b!.right).toBeLessThanOrEqual(size.width);
        expect(b!.height).toBeGreaterThan(200);
      }
      // Side by side, in this order: left, right, the panes.
      expect(two.left!.right).toBeLessThanOrEqual(two.right!.left + 1);
      expect(two.right!.right).toBeLessThanOrEqual(two.panes!.left + 1);
      // Each conversation keeps a usable width.
      expect(two.left!.width).toBeGreaterThan(280);
      expect(two.right!.width).toBeGreaterThan(280);

      // A message in each column; each gets its own reply (the demo model).
      await send(page, "left", "hello from the left");
      await send(page, "right", "hello from the right");
      await expect(page.getByTestId("harness-column-left").getByTestId("harness-reply").first()).toBeVisible({
        timeout: 60_000,
      });
      await expect(page.getByTestId("harness-column-right").getByTestId("harness-reply").first()).toBeVisible({
        timeout: 60_000,
      });
      await expect(page.getByTestId("harness-column-left").getByTestId("harness-user-bubble").last()).toHaveText(
        /hello from the left/,
      );
      await expect(page.getByTestId("harness-column-right").getByTestId("harness-user-bubble").last()).toHaveText(
        /hello from the right/,
      );
      // The focus went with the last box used: its panes are shown.
      await expect(page.getByTestId("harness-column-right")).toHaveAttribute("data-focused", "true");
      await page.getByTestId("harness-column-left").getByTestId("harness-transcript").click();
      await expect(page.getByTestId("harness-column-left")).toHaveAttribute("data-focused", "true");

      // A reload keeps both.
      await page.reload();
      await expect(page.getByTestId("harness-columns")).toHaveAttribute("data-split", "columns", { timeout: 90_000 });
      await expect(page.getByTestId("harness-column-left")).toHaveAttribute("data-session-id", first);
      await expect(page.getByTestId("harness-column-right")).toHaveAttribute("data-session-id", second);

      // × returns to one column; the address loses `beside`.
      await page.getByTestId("harness-column-close-right").click();
      await expect(page.getByTestId("harness-columns")).toHaveAttribute("data-split", "one");
      await expect(page).not.toHaveURL(/beside=/);
      expect((await layout(page)).pan).toBeLessThanOrEqual(0);
    });
  });
}

test.describe("390 px — a split shows one column and a tab per session", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("tabs switch the session shown; nothing pans", async ({ page }) => {
    await page.goto(`/chat?view=code&session=${first}&beside=${second}`);
    await expect(page.getByTestId("harness-columns")).toHaveAttribute("data-split", "tabs", { timeout: 90_000 });
    await expect(page.getByTestId("harness-split-tab-left")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("harness-column-left")).toBeVisible();
    await expect(page.getByTestId("harness-column-right")).toBeHidden();
    let l = await layout(page);
    expect(l.pan).toBeLessThanOrEqual(0);
    expect(l.left!.right).toBeLessThanOrEqual(390);
    const tabs = await page.getByTestId("harness-split-tabs").boundingBox();
    expect(tabs!.x).toBeGreaterThanOrEqual(0);
    expect(tabs!.x + tabs!.width).toBeLessThanOrEqual(390);
    await page.getByTestId("harness-split-tab-right").click();
    await expect(page.getByTestId("harness-column-right")).toBeVisible();
    await expect(page.getByTestId("harness-column-left")).toBeHidden();
    l = await layout(page);
    expect(l.pan).toBeLessThanOrEqual(0);
    expect(l.right!.right).toBeLessThanOrEqual(390);
    // A tab's × returns to one column.
    await page.getByTestId("harness-split-tab-close-left").click();
    await expect(page.getByTestId("harness-columns")).toHaveAttribute("data-split", "one");
    await expect(page.getByTestId("harness-session").first()).toHaveAttribute("data-session", second);
  });
});
