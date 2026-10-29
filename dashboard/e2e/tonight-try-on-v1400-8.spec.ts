import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * v1.400.8 — the Tonight form's "Try it on several models" is the coding
 * session box's grouped list, in a real browser against the scratch daemon,
 * the model catalog STUBBED in the browser with the user's (the v1.400.7
 * spec's: Claude Code and a keyless Anthropic, Codex and a keyless OpenAI,
 * the gateway reached as its config slot and its fleet node with 30 names
 * passed on to the cloud, the RTX box).
 *
 * At 1280 × 800 and 390 × 844, in Coding sessions › Tonight › Add a task:
 *
 *  - "Runs on: Try it on several models" shows ONE row per provider, each
 *    marked where it runs, no checkbox until one is opened;
 *  - a provider opens IN PLACE, under its row and inside the form (the form
 *    sits in a scrolling column: nothing fixed to the window), its models say
 *    local or cloud, and the last of 32 is reached by scrolling the list;
 *  - a model is ticked from the keyboard; Escape closes the provider and
 *    leaves the form; Enter in the find box sends nothing;
 *  - the page never pans sideways;
 *  - the task's `try_on` carries the CATALOG pairs (caught in the browser:
 *    the scratch daemon's catalog is the demo model, so it would refuse them).
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
/** Screenshots of each step go here when set (to look at the list). */
const OUT = process.env.IJ_TRYON_OUT || "";
async function shot(page: Page, name: string): Promise<void> {
  if (!OUT) return;
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: join(OUT, `tonight-${name}.png`) });
}
const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
};
async function stub(route: Route, body: unknown): Promise<void> {
  if (route.request().method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS });
    return;
  }
  await route.fulfill({ status: 200, headers: { ...CORS, "content-type": "application/json" }, body: JSON.stringify(body) });
}

const FOLD = JSON.parse(readFileSync(join(__dirname, "..", "__tests__", "fixtures", "harness-model-fold-v1376.json"), "utf8")) as {
  models: Record<string, unknown>[];
};
const row = (provider: string, model: string, onNet: boolean, extra: Record<string, unknown> = {}) => ({
  provider,
  model,
  available: true,
  kind: "local",
  on_this_network: onNet,
  inherited_from: null,
  ...extra,
});
const OPENROUTER = Array.from({ length: 30 }, (_, i) => `vendor-${i + 1}/a-long-routed-model-name-${i + 1}:batch`);
const MODELS = [
  ...FOLD.models,
  row("custom", "glm-5.3-flash", true),
  row("custom", "fleet", false),
  row("custom", "frontier", false),
  ...OPENROUTER.map((m) => row("custom", m, false)),
  row("fleet-custom", "glm-5.3-flash", true, { name: "Custom endpoint", source: "endpoint" }),
  row("fleet-custom", "frontier", false, { name: "Custom endpoint", source: "endpoint" }),
  row("fleet-rtx6000ada", "Qwen3.8-27B (RTX)", true, { name: "RTX6000ada", source: "endpoint", size_b: 27 }),
  { provider: "mock", model: "mock-1", available: true, kind: "local", on_this_network: true },
];

interface Box {
  left: number;
  right: number;
  top: number;
  bottom: number;
}
async function boxOf(page: Page, testId: string): Promise<Box> {
  const b = await page.getByTestId(testId).first().boundingBox();
  expect(b, `${testId} has a box`).not.toBeNull();
  return { left: b!.x, right: b!.x + b!.width, top: b!.y, bottom: b!.y + b!.height };
}
/** The browser hits the element at its centre: drawn there, not covered or
 *  clipped. */
async function hitAtCentre(page: Page, selector: string): Promise<boolean> {
  return page.evaluate((sel) => {
    const el = document.querySelector<HTMLElement>(sel);
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!hit && (el.contains(hit) || hit.contains(el) || el.closest("label")?.contains(hit) === true);
  }, selector);
}
async function pan(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

for (const { width, height } of [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  test(`at ${width}×${height}: Tonight's try-on is one row per provider, opened in place, and sends the catalog pairs`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.route(`${DAEMON}/models`, (route) => stub(route, { models: MODELS }));
    const added: Record<string, unknown>[] = [];
    await page.route(`${DAEMON}/build/night`, async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") return stub(route, {});
      if (method !== "POST") return route.fallback();
      added.push(JSON.parse(route.request().postData() || "{}"));
      await route.fulfill({
        status: 409,
        headers: { ...CORS, "content-type": "application/json" },
        body: JSON.stringify({ detail: "Caught by the test." }),
      });
    });

    await page.goto("/chat"); // v1.406.0: Chat › Code
    const tab = page.getByTestId("chat-view-code");
    await expect(tab).toBeVisible({ timeout: 90_000 });
    await tab.click();
    await expect(page.getByTestId("ij-harness-view")).toBeVisible({ timeout: 60_000 });
    await page.getByTestId("harness-tonight").click();
    await expect(page.getByTestId("tonight")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("tonight-add").click();
    await page.getByTestId("tonight-folder").fill("C:\\work\\shop");
    await page.getByTestId("tonight-words").fill("Fix the parser tests");
    await page.getByTestId("tonight-how").selectOption("try");

    const box = page.getByTestId("tonight-try-on");
    await expect(box).toBeVisible();
    await box.scrollIntoViewIfNeeded();
    const groups = box.getByTestId("harness-try-on-group");
    await expect(groups).toHaveCount(4);
    expect(await groups.evaluateAll((els) => els.map((e) => e.getAttribute("data-group")))).toEqual([
      "fleet-rtx6000ada",
      "custom",
      "claude-cli",
      "codex-cli",
    ]);
    expect(await box.getByTestId("harness-try-on-group-place").allTextContents()).toEqual([
      "local",
      "local + cloud",
      "cloud · on your plan",
      "cloud · on your plan",
    ]);
    await expect(box.getByTestId("harness-try-on-row")).toHaveCount(0);
    expect(await pan(page)).toBeLessThanOrEqual(0);
    await shot(page, `${width}-1-providers`);

    // The gateway opens IN PLACE: under its row, inside the form, nothing
    // fixed to the window.
    const server = groups.nth(1);
    await server.click();
    const panel = box.getByTestId("harness-try-on-panel");
    await expect(panel).toBeVisible();
    const rowBox = await server.boundingBox();
    const p = await panel.boundingBox();
    const f = await box.boundingBox();
    expect(rowBox && p && f).toBeTruthy();
    expect(p!.y).toBeGreaterThanOrEqual(rowBox!.y + rowBox!.height - 0.5);
    expect(p!.x).toBeGreaterThanOrEqual(f!.x - 0.5);
    expect(p!.x + p!.width).toBeLessThanOrEqual(f!.x + f!.width + 0.5);
    expect(p!.x + p!.width).toBeLessThanOrEqual(width + 0.5);
    expect(await panel.evaluate((el) => getComputedStyle(el).position)).not.toBe("fixed");
    const models = panel.getByTestId("harness-try-on-row");
    await expect(models).toHaveCount(32);
    expect(await models.first().getAttribute("data-model")).toBe("glm-5.3-flash");
    expect(new Set(await panel.getByTestId("harness-try-on-place").allTextContents())).toEqual(new Set(["local", "cloud"]));
    expect(await hitAtCentre(page, '[data-testid="tonight-try-on"] [data-testid="harness-try-on-group"][data-group="custom"]')).toBe(true);
    // The last of 32 is reached by scrolling the list, drawn where it sits.
    const last = models.last();
    await last.scrollIntoViewIfNeeded();
    expect(await last.getAttribute("data-model")).toBe(OPENROUTER[OPENROUTER.length - 1]);
    expect(
      await hitAtCentre(page, `[data-testid="tonight-try-on"] [data-model="${OPENROUTER[OPENROUTER.length - 1]}"]`),
    ).toBe(true);
    expect(await pan(page)).toBeLessThanOrEqual(0);
    await shot(page, `${width}-2-server-open`);

    // The keyboard: from the provider row, Tab reaches its first model and
    // Space ticks it; Escape closes the provider (focus back on its row) and
    // leaves the form.
    await server.scrollIntoViewIfNeeded();
    await server.focus();
    await page.keyboard.press("Tab");
    await expect(models.first()).toBeFocused();
    await page.keyboard.press("Space");
    await expect(models.first()).toBeChecked();
    await expect(box.getByTestId("harness-try-on-group-picked")).toHaveText("1 picked");
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(server).toBeFocused();
    await expect(page.getByTestId("tonight-form")).toBeVisible();

    // A model folded under Claude Code still SENDS its catalog pair.
    const claude = groups.nth(2);
    await claude.click();
    const opus = box.locator('[data-testid="harness-try-on-row"][data-provider="anthropic"][data-model="claude-opus-4-8"]');
    await opus.scrollIntoViewIfNeeded();
    await opus.check();
    expect(
      await box.getByTestId("harness-try-on-picked").getByRole("listitem").allTextContents(),
    ).toEqual(["glm-5.3-flash · Your model server", "Opus 4.8 · Claude Code"]);

    // Enter in the find box sends nothing.
    const find = box.getByTestId("harness-try-on-filter");
    await find.fill("glm");
    await find.press("Enter");
    await expect(box.getByTestId("harness-try-on-row").first()).toHaveAttribute("data-model", "glm-5.3-flash");
    expect(added).toHaveLength(0);
    await find.fill("");
    expect(await pan(page)).toBeLessThanOrEqual(0);
    await shot(page, `${width}-3-picked`);

    // Add the task: its try_on is the two catalog pairs.
    await page.getByTestId("tonight-submit").click();
    await expect(page.getByTestId("tonight-problem")).toHaveText("Caught by the test.");
    expect(added).toHaveLength(1);
    expect(added[0].try_on).toEqual([
      { provider: "custom", model: "glm-5.3-flash" },
      { provider: "anthropic", model: "claude-opus-4-8" },
    ]);
    expect(added[0].team).toBe(false);
    expect(added[0].provider).toBe("");
  });
}
