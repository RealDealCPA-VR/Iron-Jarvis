import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * v1.412.0 — Build › Code starts at the composer, in a real browser.
 *
 * The user: "to start a new session it requires that i first select a folder
 * and a harness choice. Id prefer if it instead started automatically with a
 * no folder chosen in the chat bar with a simple choose selector for folder as
 * well as a choose selector for the harness."
 *
 * The Code view's own routes (`/build/app/…`) are STUBBED in the browser (no
 * Claude Code, Codex or Pi is needed, none is started); the rest of the page
 * talks to the scratch daemon (a fresh home). At 1280 × 800 and on a 390 × 844
 * phone:
 *   - the composer is there at once, ready to type, "No folder" chosen;
 *   - every chip is inside the window, and nothing pans sideways;
 *   - picking a tool with several logins asks which account, and the
 *     question fits the window too.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
};

async function json(route: Route, body: unknown): Promise<void> {
  if (route.request().method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS });
    return;
  }
  await route.fulfill({ status: 200, headers: { ...CORS, "content-type": "application/json" }, body: JSON.stringify(body) });
}

const MODES = [
  { id: "ask", label: "Ask first", dangerous: false },
  { id: "edits", label: "Auto edits", dangerous: false },
  { id: "full", label: "Full access", dangerous: true },
];

const TOOLS = {
  harnesses: [
    {
      id: "codex",
      label: "Codex",
      installed: true,
      signed_in: true,
      modes: MODES,
      hidden_modes: [],
      models: [{ id: "", label: "Codex's default" }],
      efforts: [],
      accounts: [{ id: "p_one", name: "The only account", status: "ready" }],
    },
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      modes: MODES,
      hidden_modes: [],
      models: [
        { id: "", label: "Claude Code's default" },
        { id: "claude-opus-5-5", label: "Opus 5.5 with a rather long model name" },
      ],
      efforts: ["low", "medium", "high", "xhigh", "max"],
      accounts: [
        { id: "p_work", name: "Work account with a long name", status: "ready" },
        { id: "p_home", name: "Personal", status: "ready" },
      ],
    },
    {
      id: "pi",
      label: "Pi",
      installed: true,
      signed_in: null,
      own_sign_in: true,
      modes: [{ id: "own", label: "Pi's own rules", dangerous: false }],
      hidden_modes: [],
      models: [{ id: "", label: "Pi's default" }],
      efforts: ["low", "high"],
      accounts: [],
    },
  ],
};

async function stub(page: Page): Promise<void> {
  await page.route(`${DAEMON}/build/app/harnesses`, (r) => json(r, TOOLS));
  await page.route(
    (url) => url.href.startsWith(`${DAEMON}/build/app/sessions`) && !url.pathname.includes("/sessions/"),
    (r) => json(r, { sessions: [], work_root: "C:\\Users\\me\\Documents\\Iron Jarvis" }),
  );
}

/** How far anything laid out reaches past the window's right edge. */
async function widest(page: Page): Promise<number> {
  return page.evaluate(() => {
    let worst = 0;
    const w = window.innerWidth;
    for (const el of Array.from(document.querySelectorAll<HTMLElement>("body *"))) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (getComputedStyle(el).position === "fixed") continue;
      worst = Math.max(worst, r.right - w);
    }
    return Math.max(worst, document.documentElement.scrollWidth - w);
  });
}

/** Every chip (and an open menu) is inside the window. */
async function outside(page: Page, sel: string): Promise<string[]> {
  return page.evaluate((s) => {
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(s))) {
      const r = el.getBoundingClientRect();
      if (r.width === 0) continue;
      if (r.left < -0.5 || r.right > window.innerWidth + 0.5) out.push(`${el.dataset.testid ?? el.tagName} ${r.left}..${r.right}`);
    }
    return out;
  }, sel);
}

for (const size of [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  test(`${size.width} px — Build › Code opens on the composer, chips inside the window, the account question fits`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    await stub(page);
    await page.goto("/terminals?view=code");
    const box = page.getByTestId("code-start-box");
    await expect(box).toBeVisible({ timeout: 90_000 });
    await expect(page.getByTestId("code-start-folder")).toHaveText("No folder");
    // The last tool used is not known on a fresh page: the first installed.
    await expect(page.getByTestId("code-start-tool")).toContainText("Codex");
    // One login: no account chip.
    await expect(page.getByTestId("code-start-account")).toHaveCount(0);
    await box.fill("Fix the off-by-one in cart.py and make every test pass again");
    await expect(page.getByTestId("code-start-send")).toBeEnabled();
    await page.waitForTimeout(300);
    expect(await outside(page, '[data-testid="code-start-chips"] > *, [data-testid="code-start-composer"]')).toEqual([]);
    expect(await widest(page)).toBeLessThanOrEqual(1);

    // Claude Code has two logins: picking it asks which account, at once.
    await page.getByTestId("code-start-tool").click();
    await page.getByTestId("code-start-tool-claude-code").click();
    const menu = page.getByTestId("code-start-account-menu");
    await expect(menu).toBeVisible();
    await expect(menu).toContainText("Which Claude Code account?");
    await page.waitForTimeout(200);
    expect(await outside(page, '[data-testid="code-start-account-menu"]')).toEqual([]);
    expect(await widest(page)).toBeLessThanOrEqual(1);
    await page.getByTestId("code-start-account-p_home").click();
    await expect(page.getByTestId("code-start-account")).toContainText("Personal");
    // Its effort levels are listed; the chips still fit.
    await expect(page.getByTestId("code-start-effort")).toBeVisible();
    await page.waitForTimeout(200);
    expect(await outside(page, '[data-testid="code-start-chips"] > *')).toEqual([]);
    expect(await widest(page)).toBeLessThanOrEqual(1);
  });
}
