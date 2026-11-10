import { expect, test, type Page } from "@playwright/test";

/**
 * v1.464.0: "Starter skills" on the Templates page, in a real browser against
 * the REAL scratch daemon (fresh home): the bundles are listed, Load really
 * loads them, the Skills page shows them, and the card fits a phone.
 *
 * Only the GitHub row's two daemon routes are stubbed in the browser, so this
 * spec never reaches the network.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const WEB = `http://127.0.0.1:${process.env.IJ_E2E_WEB_PORT || 8808}`;

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, OPTIONS",
};

async function fitsTheWindow(page: Page) {
  const pan = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(pan, "the page must not pan sideways").toBeLessThanOrEqual(1);
  const card = page.getByTestId("skill-bundles");
  const box = (await card.boundingBox())!;
  for (const b of await card.getByRole("button").all()) {
    const bb = await b.boundingBox();
    if (!bb) continue;
    expect(bb.x, "a button starts inside the card").toBeGreaterThanOrEqual(box.x - 1);
    expect(bb.x + bb.width, "a button ends inside the card").toBeLessThanOrEqual(box.x + box.width + 1);
  }
}

for (const size of [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  test(`starter skills load and fit at ${size.width}px`, async ({ page }) => {
    await page.setViewportSize(size);
    await page.goto(`${WEB}/templates`);
    const card = page.getByTestId("skill-bundles");
    await expect(card).toBeVisible({ timeout: 60_000 });
    for (const id of ["business", "accounting", "marketing", "coding", "project-management", "social-media"]) {
      await expect(page.getByTestId(`skill-bundle-${id}`)).toBeVisible();
    }
    await fitsTheWindow(page);

    // Open a bundle: its five skills are listed, and the card still fits.
    const row = page.getByTestId("skill-bundle-marketing");
    await row.getByRole("button", { name: /Marketing/ }).click();
    await expect(row.getByText("campaign-brief")).toBeVisible();
    await expect(row.getByRole("checkbox")).toHaveCount(5);
    await fitsTheWindow(page);

    // The GitHub row shows its warning before anything is asked.
    await page.route(`${DAEMON}/templates/skill-bundles/github/preview`, async (route) => {
      if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: CORS });
      await route.fulfill({
        status: 200,
        headers: { ...CORS, "content-type": "application/json" },
        body: JSON.stringify({
          repo: "acme/skills",
          commit: "a".repeat(40),
          more: 0,
          warning: "These skills were written by someone else.",
          skills: [
            { path: "skills/a-very-long-skill-name-that-goes-on-and-on/SKILL.md", name: "a-very-long-skill-name-that-goes-on-and-on", description: "A long description ".repeat(12), can_load: true, why: "", other_files: 3 },
            { path: "skills/bad/SKILL.md", name: "helper", description: "x", can_load: false, why: "it holds text that reads as an attempt to override Iron Jarvis's rules", other_files: 0 },
          ],
        }),
      });
    });
    const gh = page.getByTestId("skill-bundle-github");
    await gh.getByRole("button", { name: /Load skills from GitHub/ }).click();
    await expect(page.getByTestId("skill-bundle-github-warning")).toContainText("written by someone else");
    await page.getByLabel("GitHub repository address").fill("https://github.com/acme/skills");
    await page.getByTestId("skill-bundle-github-look").click();
    await expect(page.getByTestId("skill-bundle-github-list")).toBeVisible();
    await page.getByTestId("skill-bundle-github-load").click();
    await expect(page.getByTestId("skill-bundle-github-armed")).toBeVisible();
    await fitsTheWindow(page);
  });
}

test("Load all really loads a bundle, and the Skills page shows it", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`${WEB}/templates`);
  await expect(page.getByTestId("skill-bundles")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("skill-bundle-load-accounting").click();
  await expect(page.getByTestId("skill-bundles-note")).toHaveText(
    "Loaded 5 skills. Type / in Chat to use one.",
  );
  await expect(page.getByTestId("skill-bundle-summary-accounting")).toHaveText("5 skills, all loaded");
  // The real daemon now lists them as ordinary skills.
  const rows = (await (await page.request.get(`${DAEMON}/skills`)).json()).skills as {
    name: string;
    created_by: string;
  }[];
  const mine = rows.filter((r) => r.created_by === "starter").map((r) => r.name);
  expect(mine).toContain("bank-reconciliation-review");
  expect(mine.length).toBe(5);

  await page.goto(`${WEB}/skills`);
  await expect(page.getByTestId("skills-starter-pointer")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("bank-reconciliation-review").first()).toBeVisible();
  await expect(page.getByText("Starter skill").first()).toBeVisible();

  // Remove takes two presses and takes them back out.
  await page.goto(`${WEB}/templates`);
  const remove = page.getByTestId("skill-bundle-remove-accounting");
  await remove.click();
  await page.getByTestId("skill-bundle-remove-accounting").click();
  await expect(page.getByTestId("skill-bundles-note")).toContainText("Removed 5 skills.");
  await expect(page.getByTestId("skill-bundle-summary-accounting")).toHaveText("5 skills");
});
