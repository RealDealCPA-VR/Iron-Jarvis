import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * v1.414.0 — Build › Code's right column (Changes, Usage, Files, Session) in a
 * real browser. The user: "the icons for changes, remaining usage, files and
 * sessions to right just as in the native harness off to the right."
 *
 * The Code view's own routes (`/build/app/…`) are STUBBED in the browser with
 * the shapes the real daemon answers (tests/test_build_code_panes_v1414.py);
 * nothing starts a coding tool. At 1280 × 800 the panes sit beside the
 * conversation; on a 390 × 844 phone below it — and at both sizes the four
 * tabs show, each pane opens, the conversation's box is there, and nothing is
 * wider than the window.
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

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      modes: [{ id: "ask", label: "Ask first", dangerous: false, about: "Asks before it edits files or runs commands." }],
      hidden_modes: [],
      models: [{ id: "", label: "Claude Code's default" }],
      efforts: ["low", "medium", "high"],
      accounts: [],
    },
  ],
};

const SESSION = {
  id: "app_e2e",
  harness: "claude-code",
  folder: "C:\\Users\\me\\Projects\\a-folder-with-a-rather-long-name\\shop",
  title: "Fix the off-by-one in the cart and make every test pass again",
  account: "",
  model: "claude-sonnet-5",
  effort: "low",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
  capabilities: { files: false, shell: false, browser: false, extensions: false, memory: false },
};

const longLine = "const total = items.reduce((sum, item) => sum + item.price * item.quantity, 0); // a long line";
const CHANGES = {
  source: "git",
  words:
    "Every file in this folder that differs from where git was when the session started here, committed since or not (new files included). It includes changes you or another program made meanwhile; files git ignores are not listed.",
  changes: [
    {
      path: `${SESSION.folder}\\src\\cart-with-a-long-file-name.ts`,
      rel: "src/cart-with-a-long-file-name.ts",
      name: "cart-with-a-long-file-name.ts",
      status: "modified",
      added: 1,
      removed: 1,
      diff: `--- a/src/cart.ts\n+++ b/src/cart.ts\n@@ -1,2 +1,2 @@\n x\n-${longLine}\n+${longLine} fixed`,
      by_tool: true,
    },
  ],
  files: 1,
  truncated_files: false,
  by_tool: 1,
  no_undo: "No Undo here: Claude Code made these changes itself, so use git or Claude Code to take one back.",
};

const soon = new Date(Date.now() + 2 * 3600_000).toISOString();
const USAGE = {
  harness: "claude-code",
  account: null,
  seen: {
    windows: [
      { key: "five_hour", label: "5-hour window", percent: 24, resets_at: soon },
      { key: "seven_day", label: "7-day window", percent: 27, resets_at: soon },
    ],
    at: new Date().toISOString(),
    plan: "",
  },
  totals: { replies: 2, input: 12_340, output: 1_200, cache_read: 0, cost_usd: 0.42, list_price: true, cost_reported_by: "" },
  context: { used: 39_055, window: 1_000_000, percent: 4, at: new Date().toISOString() },
};

const OVERVIEW = {
  folder: SESSION.folder,
  folder_exists: true,
  on_share: false,
  git: { repo: true, branch: "a-branch-with-a-long-name-for-the-cart-fix", detached: false },
  rules: ["CLAUDE.md", "AGENTS.md"],
  context: USAGE.context,
  tool_running: true,
  programs: [{ pid: 7, name: "node.exe", command: "node C:\\Users\\me\\Projects\\shop\\node_modules\\.bin\\vite --port 5173 --host 127.0.0.1" }],
};

async function stub(page: Page): Promise<void> {
  await page.route(`${DAEMON}/build/app/harnesses`, (r) => json(r, TOOLS));
  await page.route(
    (url) => url.href.startsWith(`${DAEMON}/build/app/sessions`),
    (r) => {
      const path = new URL(r.request().url()).pathname;
      if (path === "/build/app/sessions") return json(r, { sessions: [SESSION] });
      if (path.endsWith("/changes")) return json(r, CHANGES);
      if (path.endsWith("/usage")) return json(r, USAGE);
      if (path.endsWith("/overview")) return json(r, OVERVIEW);
      if (path.endsWith("/commands")) return json(r, { commands: [] });
      return json(r, { session: SESSION, events: [], running: null, pending: [] });
    },
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
      // A box that scrolls its own content sideways (a diff) is inside its
      // scroller; only what the page itself lays out counts.
      if (el.closest("[data-testid^='review-rows-'], pre")) continue;
      worst = Math.max(worst, r.right - w);
    }
    return Math.max(worst, document.documentElement.scrollWidth - w);
  });
}

async function inside(page: Page, testid: string): Promise<boolean> {
  return page.getByTestId(testid).evaluate((el) => {
    const r = el.getBoundingClientRect();
    return r.left >= -0.5 && r.right <= window.innerWidth + 0.5;
  });
}

for (const size of [
  { width: 1280, height: 800 },
  // v1.414.0 review: 1024 — under xl, the panes below the conversation.
  { width: 1024, height: 768 },
  { width: 390, height: 844 },
]) {
  test(`${size.width} px — Build › Code's right column: four tabs, each pane opens, nothing wider than the window`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    await stub(page);
    await page.goto(`/terminals?view=code&code=${SESSION.id}`);
    await expect(page.getByTestId("code-box")).toBeVisible({ timeout: 90_000 });
    const aside = page.getByTestId("code-panes-aside");
    await aside.scrollIntoViewIfNeeded();
    const tabs = aside.getByRole("tab");
    await expect(tabs).toHaveCount(4);
    for (const name of ["Changes", "Usage", "Files", "Session"]) {
      await expect(aside.getByRole("tab", { name })).toBeVisible();
    }
    const box = await page.getByTestId("code-session").boundingBox();
    const panes = await aside.boundingBox();
    expect(box && panes).toBeTruthy();
    if (size.width >= 1280) {
      // Beside the conversation, to its right.
      expect(panes!.x).toBeGreaterThanOrEqual(box!.x + box!.width - 1);
    } else {
      // Below it.
      expect(panes!.y).toBeGreaterThanOrEqual(box!.y + box!.height - 1);
    }

    await aside.getByRole("tab", { name: "Changes" }).click();
    await expect(page.getByTestId("changes-review")).toBeVisible();
    await expect(page.getByTestId("code-changes-no-undo")).toBeVisible();

    await aside.getByRole("tab", { name: "Usage" }).click();
    await expect(page.getByTestId("code-usage-seen")).toContainText("76% left");

    await aside.getByRole("tab", { name: "Files" }).click();
    await expect(aside.getByRole("tabpanel")).toBeVisible();

    await aside.getByRole("tab", { name: "Session" }).click();
    await expect(page.getByTestId("code-session-branch")).toContainText("On branch");
    await expect(page.getByTestId("code-session-programs")).toContainText("node.exe");

    // The arrow keys move between the tabs (and focus follows).
    await aside.getByRole("tab", { name: "Session" }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(aside.getByRole("tab", { name: "Changes" })).toHaveAttribute("aria-selected", "true");
    await expect(aside.getByRole("tab", { name: "Changes" })).toBeFocused();

    // The conversation's box still takes words with the column present.
    const typed = page.getByTestId("code-box");
    await typed.scrollIntoViewIfNeeded();
    await typed.fill("check the cart");
    await expect(typed).toHaveValue("check the cart");

    await page.waitForTimeout(300);
    expect(await inside(page, "code-panes-aside")).toBe(true);
    expect(await inside(page, "code-box")).toBe(true);
    expect(await widest(page)).toBeLessThanOrEqual(1);
  });
}
