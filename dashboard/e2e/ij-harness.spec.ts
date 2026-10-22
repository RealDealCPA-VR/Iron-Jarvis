import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * IJ harness plan Phase 6 — the screen's done-when, in a real browser against
 * the scratch daemon (port 8807, a FRESH home whose model is the demo model):
 *
 *  1. open Build and switch to the IJ harness view;
 *  2. start a session in a scratch folder;
 *  3. see a reply with a change and Undo it (the demo model's one scripted
 *     step writes RESULT.md; Undo removes it from disk);
 *  4. open a terminal from the session's Terminal tab, switch to it from the
 *     left pane (the Terminals view, focused on it) and back again;
 *  5. a faked account limit (the Phase 5 routes STUBBED in the browser with
 *     page.route — the scratch daemon has no Phase 5): the card's words, the
 *     armed "Resume when ready" with its Cancel;
 *  6. 390 px: Build never pans sideways.
 */

const DAEMON = "http://127.0.0.1:8807";

/** A stubbed cross-origin answer: the page fetches the daemon from another
 *  origin, so the stub answers the preflight and carries the CORS headers. */
const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS",
};
async function stub(route: Route, answer: () => { status?: number; contentType?: string; body: string }): Promise<boolean> {
  if (route.request().method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS });
    return false;
  }
  const a = answer();
  await route.fulfill({
    status: a.status ?? 200,
    headers: { ...CORS, "content-type": a.contentType ?? "application/json" },
    body: a.body,
  });
  return true;
}

test.describe.configure({ mode: "serial" });

let work = "";
let sessionId = "";

test.beforeAll(() => {
  work = mkdtempSync(join(tmpdir(), "ij-harness-e2e-"));
});

test.afterAll(() => {
  // A shell the daemon still holds open in the folder can lock it on
  // Windows for a moment after it closes: the scratch folder is the OS's
  // temp, so a leftover is harmless — never a failed run.
  try {
    if (work) rmSync(work, { recursive: true, force: true });
  } catch {
    /* left for the OS to clear */
  }
});

async function openHarness(page: Page) {
  await page.goto("/terminals");
  const tab = page.getByTestId("build-view-harness");
  await expect(tab).toBeVisible({ timeout: 90_000 });
  await tab.click();
  await expect(page.getByTestId("ij-harness-view")).toBeVisible({ timeout: 60_000 });
}

test("Build → IJ harness → a session → a reply with a change, and Undo", async ({ page }) => {
  await openHarness(page);
  await page.getByTestId("harness-new-session").click();
  await page.getByTestId("harness-new-folder").fill(work);
  await page.getByTestId("harness-new-start").click();
  await expect(page.getByTestId("harness-session")).toBeVisible();
  await expect(page.getByTestId("harness-folder")).toContainText(work.split(/[\\/]/).pop() as string);
  sessionId = (await page.getByTestId("harness-session").getAttribute("data-session")) ?? "";
  expect(sessionId).not.toBe("");
  // The open session is the page's stable address.
  await expect(page).toHaveURL(new RegExp(`session=${sessionId}`));

  const box = page.getByTestId("harness-input");
  await box.fill("Write a hello file");
  await box.press("Enter");
  // The reply the daemon wrote (re-read after the turn), with what it changed.
  await expect(page.getByTestId("harness-reply").last()).toContainText("saved RESULT.md", { timeout: 90_000 });
  const changes = page.getByTestId("harness-reply").last().getByTestId("changed-files");
  await expect(changes).toBeVisible({ timeout: 30_000 });
  expect(existsSync(join(work, "RESULT.md"))).toBe(true);

  // Undo: open the line, press Undo, accept the confirm — the file is gone.
  await changes.getByTestId("changed-files-line").click();
  page.once("dialog", (d) => void d.accept());
  await changes.getByTestId("changed-undo-0").click();
  await expect.poll(() => existsSync(join(work, "RESULT.md")), { timeout: 20_000 }).toBe(false);
});

test("a terminal from the left pane, and back", async ({ page }) => {
  test.skip(!sessionId, "needs the session from the first test");
  await page.goto(`/terminals?session=${sessionId}`);
  await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
  await page.getByTestId("harness-pane-tab-terminal").click();
  await page.getByTestId("pane-terminal-open").click();
  // The new terminal is a normal Build terminal: it shows in the left pane.
  const row = page.locator('[data-testid^="harness-terminal-row-"]').first();
  await expect(row).toBeVisible({ timeout: 30_000 });
  const termId = ((await row.getAttribute("data-testid")) ?? "").replace("harness-terminal-row-", "");
  await row.click();
  // The Terminals view, focused on THAT terminal.
  await expect(page.getByTestId("pane-rail")).toBeVisible();
  await expect(page.getByTestId(`rail-row-${termId}`)).toHaveAttribute("data-active", "true");
  await expect(page.getByTestId(`rail-pane-${termId}`)).toHaveCSS("visibility", "visible");
  // …and back to the same session.
  await page.getByTestId("build-view-harness").click();
  await expect(page.getByTestId("harness-session")).toHaveAttribute("data-session", sessionId);
  // Close the shell the test opened (it holds the scratch folder open).
  await page.request.delete(`${DAEMON}/terminals/${termId}`);
});

test("a faked account limit shows the card; Resume when ready waits with a Cancel", async ({ page }) => {
  test.skip(!sessionId, "needs the session from the first test");
  const calls: string[] = [];
  // Phase 5's routes, STUBBED (the scratch daemon predates them).
  await page.route(`${DAEMON}/build/accounts`, (route) =>
    stub(route, () => ({
      body: JSON.stringify({
        claude: [
          { id: "work", name: "Work", default: true, vendor: "claude", plan: "Max", status: "limit", resets_at: "2030-01-01T16:12:00Z" },
          { id: "personal", name: "Personal", default: false, vendor: "claude", plan: "Pro", status: "ready", usage: { percent: 38, window: "7 days" } },
        ],
        codex: [{ id: "cx", name: "Codex Plus", default: true, vendor: "codex", plan: "Plus", status: "ready" }],
      }),
    })),
  );
  await page.route(`${DAEMON}/build/sessions/${sessionId}/turn`, async (route) => {
    const frames = [
      ["round", { round: 0 }],
      ["account_limit", { account: "work", account_name: "Work", provider: "claude-cli", kind: "limit", resets_at: "2030-01-01T16:12:00Z" }],
      ["done", { reply: "", tools_used: [] }],
    ]
      .map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`)
      .join("");
    if (await stub(route, () => ({ contentType: "text/event-stream", body: frames }))) calls.push("turn");
  });
  await page.route(`${DAEMON}/build/sessions/${sessionId}/resume-when-ready`, async (route) => {
    const method = route.request().method();
    if (await stub(route, () => ({ body: JSON.stringify({ ok: true }) }))) calls.push(method);
  });

  await page.goto(`/terminals?session=${sessionId}`);
  await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
  const box = page.getByTestId("harness-input");
  await box.fill("Keep going");
  await box.press("Enter");
  const card = page.getByTestId("account-limit-card");
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card).toContainText(
    "This account reached its limit. Wait for it to reset, or choose another account. Your conversation and work are preserved.",
  );
  await expect(page.getByTestId("account-limit-line")).toContainText("Work · resets");
  await page.getByTestId("account-limit-resume").click();
  await expect(page.getByTestId("account-limit-waiting")).toBeVisible();
  await page.getByTestId("account-limit-cancel").click();
  await expect(page.getByTestId("account-limit-resume")).toBeVisible();
  expect(calls).toEqual(["turn", "POST", "DELETE"]);
});

test.describe("390 px — Build never pans sideways", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("the IJ harness view fits the width", async ({ page }) => {
    test.skip(!sessionId, "needs the session from the first test");
    await page.goto(`/terminals?session=${sessionId}`);
    await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
    await page.waitForTimeout(800);
    const pan = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(pan).toBeLessThanOrEqual(0);
    const shots = process.env.IJ_E2E_SHOTS;
    if (shots) await page.screenshot({ path: join(shots, "ij-harness-phone.png"), fullPage: true });
  });
  test("the Terminals view fits the width", async ({ page }) => {
    await page.goto("/terminals");
    await expect(page.getByTestId("build-view-terminals")).toBeVisible({ timeout: 90_000 });
    await page.getByTestId("build-view-terminals").click();
    await page.waitForTimeout(800);
    const pan = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(pan).toBeLessThanOrEqual(0);
  });
});
