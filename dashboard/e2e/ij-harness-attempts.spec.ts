import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * v1.397.0 review — TRY ONE TASK ON SEVERAL MODELS in a real browser against
 * the scratch daemon (a FRESH home whose model is the demo model). The demo
 * model is the only one connected there, so the session's view and the model
 * menu are STUBBED in the browser (page.route, in the daemon's own shapes:
 * harness/attempts.saved_card, GET /models):
 *
 *  1. at 390 × 844 and 1280 × 720 the attempts card — three attempts with
 *     long names, long branch names and long reasons — sits inside the
 *     window, every column inside the card, and nothing pans sideways; Keep
 *     and Discard all are reached with the keyboard alone;
 *  2. the "Try on…" chip opens ONLY on the user's press (never on load); at
 *     390 px its list sits inside the window above the chip; a row is ticked
 *     with the keyboard; Escape closes it and gives the focus back.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
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

test.describe.configure({ mode: "serial" });

let work = "";
let sessionId = "";

test.beforeAll(() => {
  work = mkdtempSync(join(tmpdir(), "ij-attempts-e2e-"));
});
test.afterAll(() => {
  try {
    if (work) rmSync(work, { recursive: true, force: true });
  } catch {
    /* the OS clears its temp folder */
  }
});

const LONG = "qwen3-coder-30b-a3b-instruct-q4_k_m-128k-context-on-the-office-box";
const GIT = (name: string) => ({
  repo: "C:\\work\\calc",
  main: "C:\\work\\calc",
  checkout: "C:\\work\\calc",
  worktree: `C:\\work\\calc.ij-worktrees\\${name}`,
  branch: `ij/${name}`,
  base: "ij/session-ab12",
  base_commit: "abc123",
  name,
});
const CARD = {
  task: "fix the add function in calc.py so the tests pass",
  base: "ij/session-ab12",
  base_commit: "abc123",
  adopt: false,
  carried: true,
  items: [
    {
      id: "try1", label: "Claude Sonnet 4.5", provider: "claude-cli", model: "claude-sonnet-4-5",
      account: "c-work", account_name: "Quarterly close work account for the reconciliation team", cost: "plan",
      branch: "ij/try-claude-sonnet-4-5-quarterly-1a2b", state: "limit", why: "limit",
      words: "Stopped at an account limit: The Claude account \"Quarterly close work account for the reconciliation team\" is at its request limit until 1:31 PM.",
      steps: 0, ms: 900, usage: { input_tokens: 0, output_tokens: 0 }, git: GIT("try-claude-sonnet-4-5-quarterly-1a2b"),
    },
    {
      id: "try2", label: "GPT-5.5 (Codex)", provider: "codex-cli", model: "gpt-5.5", account: "x-plus",
      account_name: "Codex Plus", cost: "plan", branch: "ij/try-gpt-5-5-codex-3c4d", state: "done", why: "finished",
      words: "Finished", steps: 3, ms: 42_000, tests: "passed", finish: { tests: "passed", command: "python -m unittest" },
      files: ["src/calc/operations/arithmetic/add_and_subtract.py"],
      usage: { input_tokens: 123456, output_tokens: 7890, cost_usd: 1.2345, list_price_equivalent: true },
      git: GIT("try-gpt-5-5-codex-3c4d"),
    },
    {
      id: "try3", label: LONG, provider: "ollama", model: LONG, cost: "free",
      branch: `ij/try-${LONG}-5e6f`, state: "done", why: "finished", words: "Finished", steps: 3, ms: 31_000,
      tests: "failed", files: ["calc.py", "test_calc.py"], usage: { input_tokens: 900, output_tokens: 210 },
      git: GIT(`try-${LONG}-5e6f`),
    },
  ],
};
const MODELS = [
  { provider: "claude-cli", model: "claude-sonnet-4-5", label: "Claude Sonnet 4.5", available: true, on_this_network: false, kind: "cli" },
  { provider: "codex-cli", model: "gpt-5.5", label: "GPT-5.5 (Codex)", available: true, on_this_network: false, kind: "cli" },
  { provider: "ollama", model: LONG, label: LONG, available: true, on_this_network: true, kind: "local" },
  { provider: "mock", model: "mock-1", available: true, kind: "local", on_this_network: true },
];

async function openHarness(page: Page) {
  await page.goto("/terminals");
  const tab = page.getByTestId("build-view-harness");
  await expect(tab).toBeVisible({ timeout: 90_000 });
  await tab.click();
  await expect(page.getByTestId("ij-harness-view")).toBeVisible({ timeout: 60_000 });
}

async function withCard(page: Page): Promise<void> {
  await page.route(`${DAEMON}/models`, (route) => stub(route, { models: MODELS }));
  await page.route(`${DAEMON}/build/sessions/${sessionId}`, async (route) => {
    if (route.request().method() === "OPTIONS") return stub(route, {});
    const real = await route.fetch({ url: `${DAEMON}/build/sessions/${sessionId}`, method: "GET" });
    const view = (await real.json()) as Record<string, unknown>;
    return stub(route, {
      ...view,
      messages: [
        { role: "user", content: "Try this with Claude, Codex and the local model: fix add", turnId: "t1", at: "2026-10-26T10:00:00" },
        { role: "assistant", content: "Tried this on 3 models, each on its own branch:", turnId: "t1", at: "2026-10-26T10:01:00", attempts: CARD },
      ],
    });
  });
}

test("a session to show the card in", async ({ page }) => {
  await openHarness(page);
  await page.getByTestId("harness-new-session").click();
  await page.getByTestId("harness-new-folder").fill(work);
  await page.getByTestId("harness-new-start").click();
  await expect(page.getByTestId("harness-session")).toBeVisible();
  sessionId = (await page.getByTestId("harness-session").getAttribute("data-session")) ?? "";
  expect(sessionId).not.toBe("");
});

for (const { width, height } of [
  { width: 390, height: 844 },
  { width: 1280, height: 720 },
]) {
  test(`the attempts card fits at ${width}×${height} and its presses are reached with the keyboard`, async ({ page }) => {
    test.skip(!sessionId, "needs the session from the first test");
    await page.setViewportSize({ width, height });
    await withCard(page);
    await page.goto(`/terminals?session=${sessionId}`);
    const card = page.getByTestId("attempts-card");
    await expect(card).toBeVisible({ timeout: 90_000 });
    await expect(page.getByTestId("attempt-column")).toHaveCount(3);
    await card.scrollIntoViewIfNeeded();
    const fit = await page.evaluate(() => {
      const c = document.querySelector('[data-testid="attempts-card"]')!.getBoundingClientRect();
      const cols = Array.from(document.querySelectorAll<HTMLElement>('[data-testid="attempt-column"]')).map((el) => {
        const r = el.getBoundingClientRect();
        const nodes = Array.from(el.querySelectorAll<HTMLElement>("h3, dd, dt, p, button"));
        const parts = nodes.map((p) => p.getBoundingClientRect());
        // Text that runs past its own box (a long branch that never wraps)
        // overflows even when the box itself sits inside the column.
        const spill = [el, ...nodes]
          .filter((n) => n.scrollWidth > n.clientWidth + 1)
          .map((n) => `${n.tagName.toLowerCase()} ${(n.textContent || "").trim().slice(0, 40)}`);
        return { left: r.left, right: r.right, spill, parts: parts.filter((p) => p.width > 0).map((p) => [p.left, p.right]) };
      });
      return {
        vw: document.documentElement.clientWidth,
        pan: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        card: [c.left, c.right],
        cols,
      };
    });
    expect(fit.pan, "the page pans sideways").toBeLessThanOrEqual(0);
    expect(fit.card[0]).toBeGreaterThanOrEqual(0);
    expect(fit.card[1]).toBeLessThanOrEqual(fit.vw + 0.5);
    for (const col of fit.cols) {
      expect(col.spill, "text runs past its box").toEqual([]);
      expect(col.left).toBeGreaterThanOrEqual(fit.card[0] - 0.5);
      expect(col.right).toBeLessThanOrEqual(fit.card[1] + 0.5);
      for (const [l, r] of col.parts) {
        expect(l, "a part of a column starts outside it").toBeGreaterThanOrEqual(col.left - 0.5);
        expect(r, "a part of a column runs past it").toBeLessThanOrEqual(col.right + 0.5);
      }
    }
    // The carried card says Keep lands the result, not committed.
    await expect(page.getByTestId("attempts-lead")).toContainText("not committed yet");
    // Keep is offered on the finished ones only; the presses are reached by
    // the keyboard alone (Keep, then Discard all after the last column).
    const keeps = page.getByTestId("attempt-keep");
    await expect(keeps).toHaveCount(2);
    await expect(keeps.first()).toBeEnabled();
    await keeps.first().focus();
    await expect(keeps.first()).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(keeps.nth(1)).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByTestId("attempts-discard").getByRole("button")).toBeFocused();
    // Enter arms Discard all (the first of two presses) — nothing is sent.
    const sent: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/attempts/")) sent.push(r.url());
    });
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("attempts-discard").getByRole("button")).toHaveText("Move their folders to the trash");
    expect(sent).toEqual([]);
  });
}

test("the Try on chip opens only on the user's press; at 390 px its list fits; keyboard and Escape", async ({ page }) => {
  test.skip(!sessionId, "needs the session from the first test");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route(`${DAEMON}/models`, (route) => stub(route, { models: MODELS }));
  await page.goto(`/terminals?session=${sessionId}`);
  const chip = page.getByTestId("harness-try-on");
  await expect(chip).toBeVisible({ timeout: 90_000 });
  // Never open on its own.
  await page.waitForTimeout(500);
  await expect(page.getByTestId("harness-try-on-menu")).toHaveCount(0);
  await expect(chip).toHaveAttribute("aria-expanded", "false");
  await chip.focus();
  await page.keyboard.press("Enter");
  const menu = page.getByTestId("harness-try-on-menu");
  await expect(menu).toBeVisible();
  const box = await menu.boundingBox();
  const chipBox = await chip.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(16 - 0.5);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390 - 16 + 0.5);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThanOrEqual(chipBox!.y + 0.5);
  // The demo model is never offered. v1.400.7: one row per provider, local
  // first; a provider's models open under its row on a phone.
  await expect(page.getByTestId("harness-try-on-group")).toHaveCount(3);
  const claude = page.locator('[data-testid="harness-try-on-group"][data-group="claude-cli"]');
  await claude.focus();
  await page.keyboard.press("Enter");
  const first = page.getByTestId("harness-try-on-row").first();
  await page.keyboard.press("Tab");
  await expect(first).toBeFocused();
  await page.keyboard.press("Space");
  await expect(first).toBeChecked();
  await expect(chip).toContainText("Try on Claude Sonnet 4.5");
  // Escape closes the pop-out, then the list.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("harness-try-on-panel")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(chip).toBeFocused();
  const pan = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(pan).toBeLessThanOrEqual(0);
});
