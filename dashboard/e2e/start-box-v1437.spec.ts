import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * v1.437.0 — Chat › Code's NEW-SESSION BOX carries the session composer's own
 * row (the user: "Try on is not available at all in a new chat, and neither
 * are Team, effort or the account").
 *
 * In a real browser against the scratch daemon, with GET /models and GET
 * /build/accounts stubbed in the browser (a Claude Code model with reasoning
 * levels and two accounts, a local model with levels, one of 50 characters):
 * the empty view (/chat?view=code) is the start box; its toolbar holds the
 * permission chip, "Try on…", Team (when the daemon has teams), the effort
 * chip (only for a model that offers levels), the model chip naming the
 * account it was picked under, and Send — measured at 1440 × 900,
 * 1280 × 720, 1024 × 640 and 390 × 844: never a sideways pan, every control
 * inside the card and none over another; from a 640 px card up, one vertical
 * centre, Send right-most. Try on with no folder says why when pressed.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;

const SIZES = [
  { width: 1440, height: 900 },
  { width: 1280, height: 720 },
  { width: 1024, height: 640 },
  { width: 390, height: 844 },
] as const;

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
};
async function stub(route: Route, body: string): Promise<void> {
  if (route.request().method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS });
    return;
  }
  await route.fulfill({ status: 200, headers: { ...CORS, "content-type": "application/json" }, body });
}

const MODELS = [
  {
    provider: "claude-cli",
    model: "claude-opus-5-5",
    label: "Opus 5.5",
    kind: "cli",
    available: true,
    on_this_network: false,
    reasoning: ["low", "medium", "high", "xhigh"],
    reasoning_default: "medium",
  },
  {
    provider: "fleet-rtx",
    model: "qwen3.8-27b",
    label: "Qwen3.8-27B (RTX)",
    kind: "local",
    available: true,
    on_this_network: true,
    name: "RTX-BOX",
    reasoning: ["low", "medium", "high"],
    reasoning_default: "",
  },
  {
    provider: "fleet-dgx",
    model: "llama-4-maverick-17b-128e-instruct-fp8",
    label: "Llama-4-Maverick-17B-128E-Instruct-FP8 (DGX Spark)",
    kind: "local",
    available: true,
    on_this_network: true,
    name: "DGX Spark",
  },
];
const ACCOUNTS = {
  claude: [
    { id: "c-work", name: "Work", default: true, status: "ready", plan_label: "Max" },
    { id: "c-home", name: "Home", status: "ready", plan_label: "Pro" },
  ],
  codex: [],
  grok: [],
  iron_proxy: { enabled: true, running: true },
};

async function seed(page: Page) {
  await page.route(`${DAEMON}/models`, (route) => stub(route, JSON.stringify({ models: MODELS })));
  await page.route(`${DAEMON}/build/accounts`, (route) => stub(route, JSON.stringify(ACCOUNTS)));
}

interface Ctl {
  name: string;
  left: number;
  right: number;
  top: number;
  bottom: number;
}

async function facts(page: Page) {
  return page.evaluate(() => {
    const box = document.querySelector('[data-testid="harness-new-composer"]') as HTMLElement;
    const card = box.querySelector("[data-dock-composer]") as HTMLElement;
    const bar = box.querySelector('[data-testid="harness-new-toolbar"]') as HTMLElement;
    const send = bar.querySelector('[data-testid="harness-new-send"]') as HTMLElement;
    const seen = new Set<Element>();
    const controls: Ctl[] = [];
    for (const el of Array.from(bar.querySelectorAll<HTMLElement>("button, select"))) {
      const shown = el.tagName === "SELECT" ? (el.parentElement as HTMLElement) : el;
      if (seen.has(shown)) continue;
      seen.add(shown);
      const r = shown.getBoundingClientRect();
      if (!(r.width > 0 && r.height > 0) || getComputedStyle(shown).visibility === "hidden") continue;
      const name =
        shown === send
          ? "Send"
          : el.getAttribute("data-testid") || el.getAttribute("aria-label") || (el.textContent || "").trim().slice(0, 24);
      controls.push({ name, left: r.left, right: r.right, top: r.top, bottom: r.bottom });
    }
    const c = card.getBoundingClientRect();
    return {
      pan: document.documentElement.scrollWidth - window.innerWidth,
      card: { left: c.left, right: c.right, top: c.top, bottom: c.bottom, width: c.width },
      controls,
      names: controls.map((x) => x.name),
    };
  });
}

function check(f: Awaited<ReturnType<typeof facts>>, label: string) {
  expect.soft(f.pan, `${label}: no sideways pan`).toBeLessThanOrEqual(0);
  const outside = f.controls.filter(
    (c) => c.left < f.card.left - 0.5 || c.right > f.card.right + 0.5 || c.top < f.card.top - 0.5 || c.bottom > f.card.bottom + 0.5,
  );
  expect.soft(outside.map((c) => c.name), `${label}: every control inside the card`).toEqual([]);
  const overlaps: string[] = [];
  for (let i = 0; i < f.controls.length; i++) {
    for (let j = i + 1; j < f.controls.length; j++) {
      const a = f.controls[i];
      const b = f.controls[j];
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w > 0.5 && h > 0.5) overlaps.push(`${a.name} × ${b.name}`);
    }
  }
  expect.soft(overlaps, `${label}: no two controls overlap`).toEqual([]);
  if (f.card.width < 640) return;
  const centres = f.controls.map((c) => (c.top + c.bottom) / 2);
  const spread = Math.max(...centres) - Math.min(...centres);
  expect.soft(spread, `${label}: one row (spread)`).toBeLessThanOrEqual(2);
  const send = f.controls.find((c) => c.name === "Send");
  const rightMost = Math.max(...f.controls.map((c) => c.right));
  expect.soft(!!send && send.right >= rightMost - 0.5, `${label}: Send is right-most`).toBe(true);
}

async function pickUnder(page: Page, accountId: string, model: string) {
  await page.getByTestId("harness-new-model").getByRole("button").click();
  await expect(page.getByTestId("model-menu")).toBeVisible();
  await page.getByTestId(`model-account-${accountId}`).click();
  await page.getByTestId("model-sub").getByText(model, { exact: true }).click();
  await expect(page.getByTestId("model-menu")).toHaveCount(0);
}

async function pickByName(page: Page, words: string) {
  await page.getByTestId("harness-new-model").getByRole("button").click();
  await expect(page.getByTestId("model-filter")).toBeFocused();
  await page.keyboard.type(words);
  await expect(page.getByTestId("model-match").first()).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("model-menu")).toHaveCount(0);
}

for (const size of SIZES) {
  const tag = `${size.width}x${size.height}`;
  test.describe(`the new-session box at ${tag}`, () => {
    test.use({ viewport: { width: size.width, height: size.height } });

    test("the session composer's row, one row, every control inside the box", async ({ page }) => {
      await seed(page);
      await page.goto("/chat?view=code");
      await expect(page.getByTestId("harness-new-box")).toBeVisible({ timeout: 90_000 });
      // Next's DEV overlay (its badge's portal, bottom left) sits over the
      // phone's model sheet and takes its clicks; it never exists in the app.
      await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
      const bar = page.getByTestId("harness-new-toolbar");
      await expect(bar.getByTestId("permission-chip")).toBeVisible();
      await expect(bar.getByTestId("harness-try-on")).toBeVisible();
      await expect(bar.getByTestId("harness-team")).toBeVisible();
      check(await facts(page), `${tag} default`);
      // A model picked under an account: the chip names both; its levels show.
      await pickUnder(page, "c-home", "Opus 5.5");
      await expect(page.getByTestId("model-chip-account")).toContainText("Home");
      await expect(bar.getByTestId("reasoning-level")).toHaveCount(1);
      check(await facts(page), `${tag} Opus on Home`);
      await pickByName(page, "maverick");
      await expect(bar.getByTestId("reasoning-level")).toHaveCount(0);
      check(await facts(page), `${tag} the long name`);
      await pickByName(page, "qwen3.8");
      await expect(bar.getByTestId("reasoning-level")).toHaveCount(1);
      check(await facts(page), `${tag} Qwen`);
      // Try on with no folder says why when pressed. The chip is
      // aria-disabled (it cannot be used here) yet still pressable for its
      // reason, which Playwright's own "enabled" wait would never allow.
      const tryOn = bar.getByTestId("harness-try-on");
      await expect(tryOn).toHaveAttribute("aria-disabled", "true");
      await tryOn.click({ force: true });
      await expect(page.getByTestId("harness-try-on-unavailable")).toContainText("Choose a folder first.");
      const menu = await page.getByTestId("harness-try-on-menu").boundingBox();
      expect.soft(menu && menu.x >= 0 && menu.y >= 0 && menu.x + menu.width <= size.width, `${tag}: the reason is inside the window`).toBe(true);
      await page.getByTestId("harness-try-on-done").click();
    });
  });
}
