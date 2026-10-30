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

// The daemon's own shapes (hosted/service.py): `{harnesses: [...]}`, mode ids
// ask | edits | plan | full, Codex's hidden modes with their reason.
const TOOLS = {
  harnesses: [
  {
    id: "claude-code",
    label: "Claude Code",
    installed: true,
    signed_in: true,
    version: "2.1.311",
    modes: [
      { id: "ask", label: "Ask first", dangerous: false },
      { id: "edits", label: "Auto edits", dangerous: false },
      { id: "plan", label: "Plan", dangerous: false },
      { id: "full", label: "Full access", dangerous: true },
    ],
    hidden_modes: [],
    models: [
      { id: "", label: "Claude Code's default" },
      { id: "claude-opus-5-5", label: "Opus 5.5" },
    ],
    efforts: ["low", "medium", "high"],
    accounts: [{ id: "prof_work", name: "Work account", status: "ready" }],
  },
  {
    id: "codex",
    label: "Codex",
    installed: false,
    signed_in: null,
    modes: [
      { id: "ask", label: "Ask first", dangerous: false },
      { id: "full", label: "Full access", dangerous: true },
    ],
    hidden_modes: [
      { id: "plan", label: "Plan", reason: "Codex has no plan mode." },
      { id: "edits", label: "Auto edits", reason: "Codex cannot run commands in its sandbox on Windows." },
    ],
    models: [{ id: "", label: "Codex's default" }],
    efforts: [],
    accounts: [],
  },
  // v1.411.0: Pi — one mode (its own rules), its own sign-in.
  {
    id: "pi",
    label: "Pi",
    installed: true,
    signed_in: null,
    own_sign_in: true,
    modes: [{ id: "own", label: "Pi's own rules", dangerous: false }],
    hidden_modes: [
      { id: "ask", label: "Ask first", reason: "Pi does not ask before it runs a tool. Only an extension of yours can ask, and its question comes here as a card." },
      { id: "edits", label: "Auto edits", reason: "Pi has no setting that lets edits run while commands still ask." },
      { id: "plan", label: "Plan", reason: "Pi's plan mode comes from an extension that switches itself off when you run the plan, so this menu could not stay in step with it. If you have it, type /plan instead." },
      { id: "full", label: "Full access", reason: "Pi already runs every tool without asking (Pi's own rules)." },
    ],
    models: [{ id: "", label: "Pi's default" }],
    efforts: ["off", "minimal", "low", "medium", "high", "xhigh", "max"],
    accounts: [],
  },
  ],
};

const SESSION = {
  id: "a1",
  harness: "claude-code",
  folder: "C:\\work\\a-rather-long-project-folder-name\\shop",
  title: "Fix the off-by-one in cart.py and make every test pass again",
  account: "prof_work",
  model: "claude-opus-5-5",
  effort: "high",
  mode: "ask",
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
  // The list, the archived list and a folder's saved conversations.
  await page.route(
    (url) => url.href.startsWith(`${DAEMON}/build/app/sessions`) && !url.pathname.includes("/sessions/"),
    (r) => {
      const u = new URL(r.request().url());
      if (u.searchParams.get("disk") === "1")
        return json(r, {
          sessions: [],
          disk: [
            {
              source: "disk",
              harness: "claude-code",
              harness_session_id: "c-old",
              title: "An earlier conversation with a fairly long title that must not push the list wider",
              folder: SESSION.folder,
              started: "",
              ended: "",
            },
          ],
        });
      return json(r, {
        sessions: [
          SESSION,
          { ...SESSION, id: "a2", harness: "codex", account: "", title: "Lint" },
          { ...SESSION, id: "a4", harness: "pi", account: "", mode: "own", model: "", title: "Pi: rename the helper" },
        ],
      });
    },
  );
  // A settled session (nothing asked): its composer and chips are on show.
  await page.route(`${DAEMON}/build/app/sessions/a3`, (r) =>
    json(r, {
      session: { ...SESSION, id: "a3", title: "Settled" },
      events: [EVENTS[0], EVENTS[1], { type: "done", stop: "completed" }],
      running: null,
      turn_id: null,
      pending: [],
      commands: [],
    }),
  );
  // v1.411.0: a Pi session waiting on an EXTENSION's confirm (the shape the
  // real parser writes: the running edit's card, asked every time).
  await page.route(`${DAEMON}/build/app/sessions/a4`, (r) =>
    json(r, {
      session: { ...SESSION, id: "a4", harness: "pi", account: "", mode: "own", model: "" },
      events: [
        EVENTS[0],
        { type: "tool", at: "2026-10-30T10:00:03Z", item: "call_1", kind: "edit", name: "edit", title: "Edit cart.py", status: "running" },
        {
          type: "approval",
          at: "2026-10-30T10:00:04Z",
          request_id: "req-pi",
          kind: "edit",
          title: "Edit cart.py",
          detail: "Allow this change?\nTool: edit\nPath: cart.py",
          path: "cart.py",
          diff: EVENTS[2].diff,
          can_session: false,
          once_only: true,
          tool: "edit",
        },
      ],
      running: null,
      turn_id: null,
      pending: [],
      commands: [{ name: "plan", description: "Toggle plan mode (read-only exploration)" }],
    }),
  );
  await page.route(`${DAEMON}/build/app/sessions/a1`, (r) =>
    json(r, {
      session: SESSION,
      events: EVENTS,
      events_total: 1203,
      dropped: 1200,
      running: null,
      turn_id: null,
      pending: [EVENTS[2]],
      commands: [{ name: "review", description: "Review the changes" }],
    }),
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
      // The folder in front lists the tool's own saved conversations.
      await expect(page.getByTestId("code-disk-line-c-old")).toHaveText("saved by Claude Code");
      await expect(page.getByTestId("code-dropped")).toBeVisible();
      await page.waitForTimeout(300);
      expect(await widest(page)).toBeLessThanOrEqual(1);
      await expect(page.getByTestId("code-row-line-a4")).toHaveText("Pi · Pi's own sign-in");
      // New session too (v1.412.0: the start composer and its tool list).
      await page.getByTestId("code-new").click();
      await page.getByTestId("code-start-tool").click();
      await expect(page.getByTestId("code-start-tool-codex")).toHaveAttribute("data-available", "false");
      await page.waitForTimeout(300);
      expect(await widest(page)).toBeLessThanOrEqual(1);
    });
  });
}

for (const size of [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  test(`${size.width} px — a Pi session: its own rules, its own sign-in, an extension's card that fits`, async ({ page }) => {
    await page.setViewportSize(size);
    await stubCodeView(page);
    await page.goto("/terminals?view=code&code=a4");
    const card = page.getByTestId("code-ask");
    await expect(card).toBeVisible({ timeout: 90_000 });
    await expect(card.getByTestId("code-ask-diff")).toBeVisible();
    await expect(card.getByTestId("code-ask-allow-session")).toHaveCount(0);
    await expect(card.getByTestId("code-ask-keys")).toHaveText("Click Allow once to allow it · Esc to deny");
    await page.waitForTimeout(300);
    expect(await widest(page)).toBeLessThanOrEqual(1);
    // New session for Pi: no account to choose, Pi's own rules (v1.412.0:
    // the start composer's chips).
    await page.getByTestId("code-new").click();
    await page.getByTestId("code-start-tool").click();
    await page.getByTestId("code-start-tool-pi").click();
    await expect(page.getByTestId("code-start-account-own")).toHaveText("Pi's own sign-in");
    await expect(page.getByTestId("code-start-account")).toHaveCount(0);
    await expect(page.getByTestId("code-start-mode-button")).toContainText("Pi's own rules");
    await page.getByTestId("code-start-mode-button").click();
    await expect(page.getByTestId("code-start-mode-hidden-full")).toContainText("without asking");
    await page.waitForTimeout(300);
    expect(await widest(page)).toBeLessThanOrEqual(1);
  });
}

// The review (v1.409.0): on a 360 px phone the mode menu stays inside the
// stage, whatever the chip's place in the row.
test("360 px: the mode menu fits inside the stage", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await stubCodeView(page);
  await page.goto("/terminals?view=code&code=a3");
  await expect(page.getByTestId("code-mode-chip-button")).toBeVisible({ timeout: 90_000 });
  await page.getByTestId("code-mode-chip-button").click();
  const menu = page.getByTestId("code-mode-chip-menu");
  await expect(menu).toBeVisible();
  await page.waitForTimeout(200);
  const fit = await page.evaluate(() => {
    const m = document.querySelector('[data-testid="code-mode-chip-menu"]')!.getBoundingClientRect();
    const st = document.querySelector('[data-testid="code-stage"]')!.getBoundingClientRect();
    return { left: m.left - st.left, right: st.right - m.right, pan: document.documentElement.scrollWidth - window.innerWidth };
  });
  expect(fit.left).toBeGreaterThanOrEqual(0);
  expect(fit.right).toBeGreaterThanOrEqual(0);
  expect(fit.pan).toBeLessThanOrEqual(0);
});

// Against the REAL scratch daemon (nothing stubbed): the list and New session
// read the daemon's own answers. Nothing here starts Claude Code or Codex
// (the daemon lists the tools from cached reads; a session's process starts
// only with its first message, which this never sends).
test("the real daemon: an empty list, the start composer lists Claude Code, Codex and Pi by name", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/terminals?view=code");
  await expect(page.getByTestId("code-list-empty")).toBeVisible({ timeout: 90_000 });
  // v1.412.0: the composer is there at once, no folder chosen.
  await expect(page.getByTestId("code-start-box")).toBeVisible();
  await expect(page.getByTestId("code-start-folder")).toHaveText("No folder");
  await expect(page.getByTestId("code-start-tool")).toBeEnabled({ timeout: 30_000 });
  await page.getByTestId("code-start-tool").click();
  await expect(page.getByTestId("code-start-tool-claude-code")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("code-start-tool-codex")).toBeVisible();
  await expect(page.getByTestId("code-start-tool-pi")).toBeVisible();
  // A tool that cannot start says why; one that can is offered.
  for (const id of ["claude-code", "codex", "pi"]) {
    const row = page.getByTestId(`code-start-tool-${id}`);
    if ((await row.getAttribute("data-available")) === "false") {
      await expect(page.getByTestId(`code-start-tool-why-${id}`)).not.toHaveText("");
    }
  }
});
