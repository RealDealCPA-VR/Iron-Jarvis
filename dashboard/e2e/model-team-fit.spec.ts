import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Settings › Model team (v1.394.0 review) in a real browser: at a phone's
 * width and a laptop's, with GET /team, /build/accounts and /team/starter
 * stubbed to members and accounts with LONG names, every part of a member's
 * row (its model, its role boxes, its account picker) and of a starter row
 * (its tick, its name box, its role boxes) sits inside its own box, and the
 * page never pans sideways. The role boxes and the picker are reached with
 * the keyboard alone.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const WEB = `http://127.0.0.1:${process.env.IJ_E2E_WEB_PORT || 8808}`;

const SIZES = [
  { width: 390, height: 844 },
  { width: 1280, height: 720 },
] as const;

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
  await route.fulfill({
    status: 200,
    headers: { ...CORS, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

const LONG_ACCOUNT = "Quarterly close work account for the reconciliation team";

const TEAM = {
  roles: [],
  members: [
    {
      name: "quarterly-close-reconciliation-reader",
      roster_name: "custom:quarterly-close-reconciliation-reader",
      description: "Reads the ledgers, the bank feeds and the client's notes, then summarizes what changed.",
      provider: "ollama",
      model: "qwen3-coder-30b-a3b-instruct-q4_k_m-128k-context",
      place: "network",
      cost: "free",
      roles: ["read", "code"],
      account: "",
      account_name: "",
      vendor: "",
      usage: null,
      available: true,
      paused: false,
      // v1.395.0 review: its RECORD per role, in the daemon's longest words.
      records: {
        read: { runs: 20, passed: 12, streak: 2, status: "not_good", words: "failed the last 2 in a row (12 of 20 passed)" },
        code: { runs: 20, passed: 17, streak: 0, status: "good", words: "good (17 of 20 passed)" },
      },
    },
    {
      name: "codex-coder",
      roster_name: "custom:codex-coder",
      description: "Writes code",
      provider: "codex-cli",
      model: "subscription",
      place: "cloud",
      cost: "plan",
      roles: ["code", "test"],
      account: "x-long",
      account_name: LONG_ACCOUNT,
      vendor: "codex",
      usage: { account: "x-long", account_name: LONG_ACCOUNT, status: "usage", percent: 61, window_mins: 300 },
      available: true,
      paused: false,
      records: {
        code: { runs: 2, passed: 1, streak: 0, status: "unknown", words: "too few to judge yet (1 of 2 passed)" },
        test: { runs: 0, passed: 0, streak: 0, status: "unknown", words: "no record yet" },
      },
    },
  ],
};

const ACCOUNTS = {
  claude: [],
  codex: [
    { id: "x-long", name: LONG_ACCOUNT, status: "usage" },
    { id: "x-team", name: "Codex Team", status: "ready" },
  ],
  iron_proxy: { enabled: true, running: true, needs_update: false, note: "" },
};

const STARTER = {
  rows: [
    {
      key: "ollama|qwen3-coder-30b-a3b-instruct-q4_k_m-128k-context", provider: "ollama",
      model: "qwen3-coder-30b-a3b-instruct-q4_k_m-128k-context", group: "local", roles: ["read", "code"],
      place: "this_pc", cost: "free", ticked: true, exists: null, description: "",
      name: "qwen3-coder-30b-a3b-instruct-q4_k_m-128k",
    },
    {
      key: "codex-cli|subscription", provider: "codex-cli", model: "subscription", group: "subscription",
      roles: ["code", "test"], place: "cloud", cost: "plan", ticked: false, exists: null, description: "",
      name: "codex",
    },
  ],
  paid: false,
  paid_available: 0,
  note: "",
};

async function open(page: Page, width: number, height: number): Promise<void> {
  await page.setViewportSize({ width, height });
  await page.route(`${DAEMON}/team`, (route) => stub(route, TEAM));
  await page.route(`${DAEMON}/build/accounts`, (route) => stub(route, ACCOUNTS));
  await page.route(`${DAEMON}/team/starter*`, (route) => stub(route, STARTER));
  await page.goto(`${WEB}/settings?section=models-team`);
  await expect(page.getByTestId("team-member-codex-coder")).toBeVisible({ timeout: 30_000 });
}

interface Box {
  id: string;
  left: number;
  right: number;
  parts: { what: string; left: number; right: number }[];
}

async function boxes(page: Page, selector: string): Promise<{ boxes: Box[]; pan: number }> {
  return page.evaluate((sel) => {
    const out: Box[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(sel))) {
      const r = el.getBoundingClientRect();
      const parts = Array.from(el.querySelectorAll<HTMLElement>("select, input, label, span, p")).map((p) => {
        const b = p.getBoundingClientRect();
        return { what: `${p.tagName.toLowerCase()} ${(p.textContent || p.getAttribute("data-testid") || "").trim().slice(0, 40)}`, left: b.left, right: b.right };
      });
      out.push({ id: el.dataset.testid || "", left: r.left, right: r.right, parts });
    }
    return { boxes: out, pan: document.documentElement.scrollWidth - window.innerWidth };
  }, selector);
}

function inside(found: Box[]): void {
  for (const box of found) {
    for (const part of box.parts) {
      if (part.right - part.left <= 0) continue;
      expect(part.left, `${box.id}: ${part.what} starts outside its box`).toBeGreaterThanOrEqual(box.left - 0.5);
      expect(part.right, `${box.id}: ${part.what} runs past its box`).toBeLessThanOrEqual(box.right + 0.5);
    }
  }
}

for (const { width, height } of SIZES) {
  test(`a member's row and a starter row fit at ${width}×${height}`, async ({ page }) => {
    await open(page, width, height);
    const members = await boxes(page, "[data-testid^='team-member-']");
    expect(members.pan, "the page pans sideways").toBeLessThanOrEqual(0);
    expect(members.boxes.map((b) => b.id).sort()).toEqual([
      "team-member-codex-coder",
      "team-member-quarterly-close-reconciliation-reader",
    ]);
    inside(members.boxes);
    // v1.395.0 review: each member's record line is drawn, inside its row.
    await expect(page.getByTestId("team-record-quarterly-close-reconciliation-reader")).toContainText(
      "reading: failed the last 2 in a row (12 of 20 passed)",
    );
    await expect(page.getByTestId("team-record-codex-coder")).toContainText("testing: no record yet");

    await page.getByTestId("team-starter-open").click();
    await expect(page.getByTestId("team-starter")).toBeVisible({ timeout: 20_000 });
    const rows = await boxes(page, "[data-testid^='team-starter-row-']");
    expect(rows.pan, "the page pans sideways with the starter open").toBeLessThanOrEqual(0);
    expect(rows.boxes).toHaveLength(2);
    inside(rows.boxes);
  });
}

test("the role boxes and the account picker are reached with the keyboard", async ({ page }) => {
  await open(page, 1280, 720);
  const first = page.getByTestId("team-role-codex-coder-read");
  await first.focus();
  await expect(first).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByTestId("team-role-codex-coder-code")).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(page.getByTestId("team-account-codex-coder")).toBeFocused();
});
