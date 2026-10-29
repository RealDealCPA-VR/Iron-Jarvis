import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * v1.409.0 — Build's Code | Terminal switch and its Code view (the real
 * Claude Code / Codex in a window), in a real browser. The Code view's own
 * routes (`/build/app/…`) are STUBBED in the browser, so this needs neither
 * Claude Code nor Codex on the machine and never starts one; the rest of
 * the page talks to the scratch daemon (a fresh home).
 *
 *  - the switch sits in the header's centre and does NOT move between the
 *    two views (the v1.370.2 rule, in pixels);
 *  - at 1280 × 800 and on a 390 × 844 phone nothing is wider than the
 *    window in either view, with a session open and its question showing;
 *  - the session list names each row's tool and account.
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

const TOOLS = [
  {
    id: "claude-code",
    label: "Claude Code",
    installed: true,
    signed_in: true,
    version: "2.1.311",
    modes: [
      { id: "default", label: "Ask first", dangerous: false },
      { id: "acceptEdits", label: "Auto edits", dangerous: false },
      { id: "bypassPermissions", label: "Full access", dangerous: true },
    ],
    models: [{ id: "claude-opus-5-5", label: "Opus 5.5" }],
    efforts: ["low", "medium", "high"],
    accounts: [{ id: "prof_work", name: "Work account", status: "ready" }],
  },
  { id: "codex", label: "Codex", installed: false, signed_in: null, modes: [], models: [], efforts: [], accounts: [] },
];

const SESSION = {
  id: "a1",
  harness: "claude-code",
  folder: "C:\\work\\a-rather-long-project-folder-name\\shop",
  title: "Fix the off-by-one in cart.py and make every test pass again",
  account: "prof_work",
  model: "claude-opus-5-5",
  effort: "high",
  mode: "default",
  harness_session_id: "5f0c2a1e",
  created_at: "2026-10-29T10:00:00Z",
  updated_at: "2026-10-29T10:00:00Z",
  archived_at: null,
  last_error: "",
};

const EVENTS = [
  { type: "user", at: "2026-10-29T10:00:01Z", text: "Fix the off-by-one in cart.py" },
  { type: "tool", at: "2026-10-29T10:00:03Z", item: "tu1", kind: "read", name: "Read", title: "Read cart.py", status: "done", output: "def total(items): ..." },
  {
    type: "approval",
    at: "2026-10-29T10:00:05Z",
    request_id: "req-1",
    kind: "edit",
    title: "Edit cart.py",
    path: "C:\\work\\a-rather-long-project-folder-name\\shop\\cart.py",
    diff: "--- a/cart.py\n+++ b/cart.py\n@@ -3,1 +3,1 @@\n-    for i in range(len(items) - 1):  # a long line that would push the card wider than a phone if it did not wrap or scroll\n+    for i in range(len(items)):\n",
    can_session: true,
  },
];

async function stubCodeView(page: Page): Promise<void> {
  await page.route(`${DAEMON}/build/app/harnesses`, (r) => json(r, TOOLS));
  await page.route(`${DAEMON}/build/app/sessions`, (r) =>
    json(r, { sessions: [SESSION, { ...SESSION, id: "a2", harness: "codex", account: "", title: "Lint", source: "disk" }] }),
  );
  await page.route(`${DAEMON}/build/app/sessions/a1`, (r) =>
    json(r, { session: SESSION, events: EVENTS, running: true, pending: [] }),
  );
}

async function switchBox(page: Page) {
  return page.evaluate(() => {
    const sw = document.querySelector('[data-testid="build-view-switch"]')!.getBoundingClientRect();
    // The page header (PageHeader's centred grid: title | centre | actions,
    // the two sides equal) — its middle is the column's, beside the sidebar.
    const head = document.querySelector('[data-testid="page-header-centred"]')!.getBoundingClientRect();
    return {
      centre: sw.left + sw.width / 2,
      top: sw.top,
      left: sw.left,
      pageCentre: head.left + head.width / 2,
      pan: document.documentElement.scrollWidth - window.innerWidth,
    };
  });
}

async function widest(page: Page): Promise<number> {
  return page.evaluate(() => {
    let worst = 0;
    const w = window.innerWidth;
    for (const el of Array.from(document.querySelectorAll<HTMLElement>("body *"))) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      // Only what is laid out on the page (a scroller's own content may run on).
      if (getComputedStyle(el).position === "fixed") continue;
      worst = Math.max(worst, r.right - w);
    }
    return Math.max(worst, document.documentElement.scrollWidth - w);
  });
}

for (const size of [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  test.describe(`${size.width} px — Build's Code | Terminal`, () => {
    test.use({ viewport: size });

    test("the switch is in the header, centred at lg, and does not move between the views", async ({ page }) => {
      await stubCodeView(page);
      await page.goto("/terminals");
      await expect(page.getByTestId("build-view-switch")).toBeVisible({ timeout: 90_000 });
      await expect(page.getByTestId("build-view-terminal")).toHaveAttribute("aria-selected", "true");
      await expect(page.getByTestId("header-new-terminal")).toBeVisible();
      await page.waitForTimeout(400);
      const terminal = await switchBox(page);
      await page.getByTestId("build-view-code").click();
      await expect(page.getByTestId("build-code-view")).toBeVisible({ timeout: 60_000 });
      await expect(page).toHaveURL(/view=code/);
      await page.waitForTimeout(400);
      const code = await switchBox(page);
      if (size.width >= 1024) {
        // The header's three columns: the switch is in its middle.
        expect(Math.abs(terminal.centre - terminal.pageCentre)).toBeLessThanOrEqual(1.5);
      }
      // Fixed: the same place in both views.
      expect(Math.abs(code.left - terminal.left)).toBeLessThanOrEqual(1);
      expect(Math.abs(code.top - terminal.top)).toBeLessThanOrEqual(1);
      expect(terminal.pan).toBeLessThanOrEqual(0);
      expect(code.pan).toBeLessThanOrEqual(0);
    });

    test("a session open with its question showing: nothing is wider than the window; rows name tool and account", async ({ page }) => {
      await stubCodeView(page);
      await page.goto("/terminals?view=code&code=a1");
      await expect(page.getByTestId("code-ask")).toBeVisible({ timeout: 90_000 });
      await expect(page.getByTestId("code-row-line-a1")).toHaveText("Claude Code · Work account");
      await expect(page.getByTestId("code-row-line-a2")).toContainText("Codex · this PC's sign-in");
      await page.waitForTimeout(300);
      expect(await widest(page)).toBeLessThanOrEqual(1);
      // New session too (the folder tree and the tool list).
      await page.getByTestId("code-new").click();
      await expect(page.getByTestId("code-new-tool-codex")).toHaveAttribute("data-available", "false");
      await page.waitForTimeout(300);
      expect(await widest(page)).toBeLessThanOrEqual(1);
    });
  });
}
