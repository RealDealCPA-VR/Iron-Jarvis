import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * v1.400.7 — the "Try on…" list GROUPED BY PROVIDER, in a real browser
 * against the scratch daemon (a fresh home whose model is the demo model; the
 * model catalog is STUBBED in the browser with the user's: Claude Code and a
 * keyless Anthropic, Codex and a keyless OpenAI — the real daemon's rows from
 * the v1.376.0 fold fixture — the user's LiteLLM gateway reached as both its
 * config slot and its fleet node, passing OpenRouter names on to the cloud,
 * and the RTX box).
 *
 * At 1280 × 720 and 390 × 844, in a coding session's box:
 *
 *  - the list opens on the user's press with ONE row per provider, each
 *    marked where it runs, no checkbox until a provider is opened, inside the
 *    window, and the page never pans sideways;
 *  - a provider's pop-out is drawn where the browser hits it (never clipped):
 *    beside the list on the RIGHT on a wide window (the chip sits at the
 *    box's left), under its row inside the list on a phone; its models say
 *    "local" / "cloud", and the long list's last model can be scrolled to;
 *  - a model is ticked from the keyboard; Escape closes the pop-out, then
 *    the list, and gives the focus back;
 *  - the message's `try_on` carries the CATALOG pairs (a model folded under
 *    Claude Code still sends `anthropic::…`), the turn request captured in
 *    the browser before it reaches the scratch daemon.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
/** Screenshots of each step go here when set (to look at the list). */
const OUT = process.env.IJ_TRYON_OUT || "";
async function shot(page: Page, name: string): Promise<void> {
  if (!OUT) return;
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: join(OUT, `${name}.png`) });
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

test.describe.configure({ mode: "serial" });

let work = "";
let sessionId = "";

test.beforeAll(async ({ request }) => {
  work = mkdtempSync(join(tmpdir(), "ij-tryon-e2e-"));
  const made = await request.post(`${DAEMON}/build/sessions`, { data: { folder: work, branch: false } });
  expect(made.ok()).toBe(true);
  sessionId = String(((await made.json()) as { id?: string }).id ?? "");
  expect(sessionId).not.toBe("");
});
test.afterAll(() => {
  try {
    if (work) rmSync(work, { recursive: true, force: true });
  } catch {
    /* the OS clears its temp folder */
  }
});

interface Box {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

async function boxOf(page: Page, testId: string): Promise<Box> {
  const b = await page.getByTestId(testId).boundingBox();
  expect(b, `${testId} has a box`).not.toBeNull();
  return { left: b!.x, right: b!.x + b!.width, top: b!.y, bottom: b!.y + b!.height };
}

/** The browser hits `testId` at its top and bottom edges and its centre:
 *  drawn there, not clipped by anything around it. */
async function drawnWhereItSits(page: Page, testId: string): Promise<boolean> {
  return page.evaluate((id) => {
    const el = document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const x = r.left + r.width / 2;
    return [r.top + 3, r.top + r.height / 2, r.bottom - 3].every((y) => {
      const hit = document.elementFromPoint(x, y);
      return !!hit && el.contains(hit);
    });
  }, testId);
}

async function pan(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

for (const { width, height } of [
  { width: 1280, height: 720 },
  { width: 390, height: 844 },
]) {
  test(`at ${width}×${height}: provider rows, a pop-out per provider, keyboard, and the catalog pairs sent`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.route(`${DAEMON}/models`, (route) => stub(route, { models: MODELS }));
    // The turn is caught in the browser (the scratch daemon's catalog is the
    // demo model, so it would refuse these picks): its body is what we check.
    const turns: Record<string, unknown>[] = [];
    await page.route(`${DAEMON}/build/sessions/${sessionId}/turn`, async (route) => {
      if (route.request().method() === "OPTIONS") return stub(route, {});
      turns.push(JSON.parse(route.request().postData() || "{}"));
      await route.fulfill({
        status: 409,
        headers: { ...CORS, "content-type": "application/json" },
        body: JSON.stringify({ detail: "Caught by the test." }),
      });
    });
    await page.goto(`/terminals?session=${sessionId}`);
    const chip = page.getByTestId("harness-try-on");
    await expect(chip).toBeVisible({ timeout: 90_000 });
    await expect(page.getByTestId("harness-try-on-menu")).toHaveCount(0);
    await chip.scrollIntoViewIfNeeded();
    await chip.click();

    const menu = page.getByTestId("harness-try-on-menu");
    await expect(menu).toBeVisible();
    // One row per provider, local first; no checkbox yet.
    const groups = page.getByTestId("harness-try-on-group");
    await expect(groups).toHaveCount(4);
    expect(await groups.evaluateAll((els) => els.map((e) => e.getAttribute("data-group")))).toEqual([
      "fleet-rtx6000ada",
      "custom",
      "claude-cli",
      "codex-cli",
    ]);
    expect(await page.getByTestId("harness-try-on-group-place").allTextContents()).toEqual([
      "local",
      "local + cloud",
      "cloud · on your plan",
      "cloud · on your plan",
    ]);
    await expect(page.getByTestId("harness-try-on-row")).toHaveCount(0);
    const m = await boxOf(page, "harness-try-on-menu");
    expect(m.left).toBeGreaterThanOrEqual(0);
    expect(m.right).toBeLessThanOrEqual(width + 0.5);
    expect(m.top).toBeGreaterThanOrEqual(0);
    expect(await drawnWhereItSits(page, "harness-try-on-menu")).toBe(true);
    await shot(page, `${width}-1-providers`);

    // The gateway: one group, its models local first, the fleet alias and
    // the twin's rows not listed again.
    const server = groups.nth(1);
    await server.click();
    const panel = page.getByTestId("harness-try-on-panel");
    await expect(panel).toBeVisible();
    const p = await boxOf(page, "harness-try-on-panel");
    const menuNow = await boxOf(page, "harness-try-on-menu");
    expect(p.left).toBeGreaterThanOrEqual(0);
    expect(p.right).toBeLessThanOrEqual(width + 0.5);
    expect(p.top).toBeGreaterThanOrEqual(0);
    expect(p.bottom).toBeLessThanOrEqual(height + 0.5);
    if (width >= 640) {
      // Beside the list, on its right: over nothing of the list.
      expect(p.left).toBeGreaterThanOrEqual(menuNow.right - 0.5);
    } else {
      // Under its row, inside the list.
      expect(p.left).toBeGreaterThanOrEqual(menuNow.left - 0.5);
      expect(p.right).toBeLessThanOrEqual(menuNow.right + 0.5);
    }
    expect(await drawnWhereItSits(page, "harness-try-on-panel")).toBe(true);
    await shot(page, `${width}-2-server`);
    const rows = panel.getByTestId("harness-try-on-row");
    await expect(rows).toHaveCount(2 + OPENROUTER.length);
    expect(await rows.evaluateAll((els) => els.map((e) => e.getAttribute("data-provider")))).toEqual(
      Array(2 + OPENROUTER.length).fill("custom"),
    );
    expect(await rows.first().getAttribute("data-model")).toBe("glm-5.3-flash");
    expect(await panel.getByTestId("harness-try-on-place").first().textContent()).toBe("local");
    expect(await panel.getByTestId("harness-try-on-place").last().textContent()).toBe("cloud");
    await expect(panel).not.toContainText(/your model server/i);
    // The long list's last model is reached by scrolling the pop-out.
    const lastReached = await page.evaluate(() => {
      const list = Array.from(
        document.querySelectorAll<HTMLInputElement>('[data-testid="harness-try-on-panel"] [data-testid="harness-try-on-row"]'),
      );
      const last = list[list.length - 1];
      last.scrollIntoView({ block: "nearest" });
      const r = last.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return !!hit && (hit === last || hit.contains(last) || last.closest("label")!.contains(hit));
    });
    expect(lastReached).toBe(true);
    // Ticked from the keyboard: the provider row, then Tab into its models.
    await server.focus();
    await page.keyboard.press("Tab");
    await expect(rows.first()).toBeFocused();
    await page.keyboard.press("Space");
    await expect(rows.first()).toBeChecked();
    await expect(page.getByTestId("harness-try-on-group-picked")).toHaveText("1 picked");

    // Claude Code: each model once; a keyless Anthropic's own one sits here.
    await groups.nth(2).click();
    const cc = page.getByTestId("harness-try-on-panel");
    // v1.401.2: 7 — "Your Claude plan" is Opus 5.5 (the default), listed once.
    await expect(cc.getByTestId("harness-try-on-row")).toHaveCount(7);
    // Drawn where it sits as it opens (on a phone: brought into view, under
    // its row, inside the list).
    expect(await drawnWhereItSits(page, "harness-try-on-panel")).toBe(true);
    await shot(page, `${width}-3-claude`);
    expect(await cc.getByTestId("harness-try-on-place").allTextContents()).toEqual(Array(7).fill("cloud"));
    const opus48 = cc.locator('[data-testid="harness-try-on-row"][data-model="claude-opus-4-8"]');
    await expect(opus48).toHaveAttribute("data-provider", "anthropic");
    await opus48.check();
    await expect(page.getByTestId("harness-try-on-picked")).toContainText("Opus 4.8 · Claude Code");
    await shot(page, `${width}-4-picked`);
    expect(await pan(page), "the page pans sideways").toBeLessThanOrEqual(0);

    // Escape: the pop-out, then the list; the focus comes back each time.
    await opus48.focus();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("harness-try-on-panel")).toHaveCount(0);
    await expect(groups.nth(2)).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(chip).toBeFocused();
    await expect(chip).toHaveAttribute("aria-label", "Try on 2 models");

    // The next message carries the catalog pairs.
    const box = page.getByTestId("harness-input");
    await box.fill("fix add in calc.py");
    await box.press("Enter");
    await expect.poll(() => turns.length, { timeout: 20_000 }).toBeGreaterThan(0);
    expect(turns[0].try_on).toEqual([
      { provider: "custom", model: "glm-5.3-flash" },
      { provider: "anthropic", model: "claude-opus-4-8" },
    ]);
    expect(await pan(page), "the page pans sideways").toBeLessThanOrEqual(0);
  });
}
