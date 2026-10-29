import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type APIRequestContext, type Page, type Route } from "@playwright/test";

/**
 * A coding session's model chip, effort list and pane tabs, in a real
 * browser against the scratch daemon, the model catalog STUBBED in the
 * browser with the real daemon's Claude rows (the plan row has no label; the
 * live picker marks Opus 5.5 as the CLI's default and lists Extra for it).
 *
 *  - "Your Claude plan" is gone: a session on the plan's default names the
 *    model ("Opus 5.5"), and the menu lists that model once;
 *  - the effort list is Low, Medium, High, Extra — no Default — and picking
 *    Extra saves "xhigh" on the session;
 *  - the six pane tabs are icons: no word shows until the pointer RESTS on a
 *    tab (not at once, so a passing pointer shows nothing) or a tab gets
 *    keyboard focus; the word is drawn inside the window and not cut off by
 *    the pane; it goes when the pointer leaves.
 *
 * At 1280 × 800 and 1600 × 900 (the panes beside the conversation), and
 * 390 × 844 (no sideways pan). Screenshots go to IJ_PANE_ICONS_OUT when set.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const OUT = process.env.IJ_PANE_ICONS_OUT || "";
const XHIGH = ["low", "medium", "high", "xhigh"];
const BASE = ["low", "medium", "high"];
const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
};
const row = (provider: string, model: string, extra: Record<string, unknown> = {}) => ({
  provider,
  model,
  kind: "cli",
  available: true,
  inherited_from: null,
  ...extra,
});
const MODELS = [
  row("claude-cli", "subscription", { reasoning: XHIGH, reasoning_default: "" }),
  row("claude-cli", "claude-opus-5-5", { label: "Opus 5.5", cli_default: true, reasoning: XHIGH }),
  row("claude-cli", "claude-fable-5-1", { label: "Fable 5.1", cli_default: false, reasoning: XHIGH }),
  row("claude-cli", "claude-sonnet-5-5", { label: "Sonnet 5.5", cli_default: false, reasoning: XHIGH }),
  row("claude-cli", "claude-haiku-4-5-20251001", { label: "Haiku 4.5", cli_default: false, reasoning: BASE }),
  row("codex-cli", "subscription", { reasoning: BASE }),
  row("codex-cli", "gpt-6", { reasoning: BASE }),
  { provider: "mock", model: "mock-1", available: true, kind: "local", on_this_network: true },
];
// v1.404.0: Session joined after Files.
const TABS = ["plan", "timeline", "terminal", "changes", "browser", "files", "session"] as const;
const WORDS = ["Plan", "Timeline", "Terminal", "Changes", "Browser", "Files", "Session"];

async function stub(route: Route, body: unknown): Promise<void> {
  if (route.request().method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS });
    return;
  }
  await route.fulfill({ status: 200, headers: { ...CORS, "content-type": "application/json" }, body: JSON.stringify(body) });
}
async function shot(page: Page, name: string): Promise<void> {
  if (!OUT) return;
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: join(OUT, `${name}.png`) });
}
const opacity = (page: Page, tab: string) =>
  page.getByTestId(`harness-pane-tip-${tab}`).evaluate((el) => Number(getComputedStyle(el).opacity));
async function pan(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}
/** The tip's box, the window, and every ancestor that clips: the tip must lie
 *  inside each (an `overflow` ancestor would cut it off where it is drawn). */
async function tipInsideEverything(page: Page, tab: string): Promise<string[]> {
  return page.getByTestId(`harness-pane-tip-${tab}`).evaluate((el) => {
    const r = el.getBoundingClientRect();
    const bad: string[] = [];
    if (r.left < 0 || r.top < 0 || r.right > window.innerWidth || r.bottom > window.innerHeight) bad.push("window");
    for (let a = el.parentElement; a; a = a.parentElement) {
      const s = getComputedStyle(a);
      if ([s.overflowX, s.overflowY].some((o) => o !== "visible")) {
        const b = a.getBoundingClientRect();
        if (r.left < b.left - 0.5 || r.right > b.right + 0.5 || r.top < b.top - 0.5 || r.bottom > b.bottom + 0.5) {
          bad.push(`${a.tagName.toLowerCase()}.${String(a.className).split(" ").slice(0, 3).join(".")}`);
        }
      }
    }
    return bad;
  });
}

let work = "";

test.beforeAll(() => {
  work = mkdtempSync(join(tmpdir(), "ij-pane-icons-"));
});

/** A fresh session on the plan's own default (nothing picked for effort),
 *  one per test: a run before this one must not have picked a level. */
async function newSession(request: APIRequestContext): Promise<string> {
  const a = await request.post(`${DAEMON}/build/sessions`, { data: { folder: work, branch: false } });
  expect(a.ok()).toBe(true);
  const id = ((await a.json()) as { id: string }).id;
  const p = await request.patch(`${DAEMON}/build/sessions/${id}`, {
    data: { provider: "claude-cli", model: "subscription" },
  });
  expect(p.ok(), await p.text()).toBe(true);
  return id;
}

test.afterAll(() => {
  try {
    if (work) rmSync(work, { recursive: true, force: true });
  } catch {
    /* the OS clears temp */
  }
});

async function open(page: Page, session: string): Promise<string[]> {
  await page.route(`${DAEMON}/models`, (route) => stub(route, { models: MODELS }));
  const patches: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "PATCH" && req.url().includes(`/build/sessions/${session}`)) patches.push(req.postData() || "");
  });
  await page.goto(`/terminals?session=${session}`);
  await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
  return patches;
}

for (const { width, height } of [
  { width: 1280, height: 800 },
  { width: 1600, height: 900 },
]) {
  test(`at ${width}×${height}: the plan row names its model, Extra is offered, the pane words show only on hover or focus`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height });
    const patches = await open(page, await newSession(request));

    // ---- the model chip names the model the plan runs
    const chip = page.getByTitle("Switch model");
    await expect(chip).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("model-chip-words").first()).toHaveText("Opus 5.5", { timeout: 30_000 });

    // ---- the menu lists that model once, never "Your Claude plan"
    const menu = page.getByTestId("model-menu");
    for (let i = 0; i < 5 && (await menu.count()) === 0; i++) {
      await chip.click();
      await menu.waitFor({ state: "visible", timeout: 4_000 }).catch(() => {});
    }
    await expect(menu).toBeVisible();
    await expect(menu).not.toContainText("Your Claude plan");
    await page.getByTestId("model-filter").fill("claude");
    const matches = menu.getByTestId("model-match");
    await expect(matches.first()).toBeVisible();
    const words = await matches.allTextContents();
    expect(words.filter((w) => w.includes("Opus 5.5"))).toHaveLength(1);
    expect(words.some((w) => /Your Claude plan|subscription/i.test(w))).toBe(false);
    await shot(page, `menu-${width}`);
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);

    // ---- the effort list: the levels and Extra, no Default; Extra is saved
    const effort = page.getByTestId("reasoning-level").first();
    await expect(effort).toBeAttached();
    const visibleOptions = await effort.evaluate((el) =>
      Array.from((el as HTMLSelectElement).options)
        .filter((o) => !o.hidden)
        .map((o) => o.textContent),
    );
    expect(visibleOptions).toEqual(["Low", "Medium", "High", "Extra"]);
    await effort.selectOption("xhigh");
    await expect.poll(() => patches.some((b) => b.includes('"reasoning":"xhigh"')), { timeout: 15_000 }).toBe(true);
    await expect(page.getByTestId("reasoning-level").first().locator("xpath=..")).toContainText("Extra");

    // ---- the pane tabs: icons, no word until the pointer rests or a key focuses
    const tabs = page.getByTestId("harness-pane-tabs");
    await expect(tabs).toBeVisible();
    await page.mouse.move(2, height - 2);
    for (const t of TABS) {
      await expect(page.getByTestId(`harness-pane-tab-${t}`)).toBeVisible();
      await expect.poll(() => opacity(page, t)).toBe(0);
    }
    // Each tab keeps its word as its name.
    for (let i = 0; i < TABS.length; i++) {
      await expect(tabs.getByRole("tab", { name: new RegExp(`^${WORDS[i]}`) })).toHaveAttribute(
        "data-testid",
        `harness-pane-tab-${TABS[i]}`,
      );
    }
    await shot(page, `tabs-rest-${width}`);

    // v1.404.0: and the rightmost, Session — the tip most likely to be cut.
    for (const t of ["plan", "files", "session"] as const) {
      const box = (await page.getByTestId(`harness-pane-tab-${t}`).boundingBox())!;
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      // A passing pointer shows nothing: still faded just after it arrives.
      expect(await opacity(page, t)).toBeLessThan(0.5);
      // Resting there shows the word, inside the window, not cut off.
      await expect.poll(() => opacity(page, t), { timeout: 3_000 }).toBe(1);
      expect(await tipInsideEverything(page, t)).toEqual([]);
      await shot(page, `tip-${t}-${width}`);
      // Away again: gone.
      await page.mouse.move(2, height - 2);
      await expect.poll(() => opacity(page, t), { timeout: 3_000 }).toBe(0);
    }

    // The keyboard: the arrow key moves focus, and the focused tab's word shows.
    await page.getByTestId("harness-pane-tab-plan").click();
    await page.mouse.move(2, height - 2);
    await page.keyboard.press("ArrowRight");
    await expect(page.getByTestId("harness-pane-tab-timeline")).toBeFocused();
    await expect.poll(() => opacity(page, "timeline"), { timeout: 3_000 }).toBe(1);
    expect(await opacity(page, "plan")).toBe(0);
    expect(await tipInsideEverything(page, "timeline")).toEqual([]);
    await shot(page, `tip-focus-${width}`);
    expect(await pan(page)).toBeLessThanOrEqual(0);
  });
}

test("at 390×844: the effort list and the pane tabs never pan the page", async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, await newSession(request));
  await expect(page.getByTestId("model-chip-words").first()).toHaveText("Opus 5.5", { timeout: 60_000 });
  const tabs = page.getByTestId("harness-pane-tabs");
  if (await tabs.isVisible()) {
    for (const t of TABS) await expect.poll(() => opacity(page, t)).toBe(0);
    await page.getByTestId("harness-pane-tab-files").focus();
    await page.keyboard.press("ArrowLeft");
    await expect.poll(() => opacity(page, "browser"), { timeout: 3_000 }).toBe(1);
    expect(await tipInsideEverything(page, "browser")).toEqual([]);
  }
  await shot(page, "phone-390");
  expect(await pan(page)).toBeLessThanOrEqual(0);
});
