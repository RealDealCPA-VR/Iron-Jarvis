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
 *  5. a faked account limit — the Phase 5 routes STUBBED in the browser with
 *     page.route, in Phase 5's own shapes (v1.368.0), so the test runs the
 *     same against a daemon with or without Phase 5: the card's words, the
 *     armed "Resume when ready" surviving a reload, its Cancel, and the
 *     daemon's resume (the page rejoins the resumed turn and the card goes);
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
  if (process.env.IJ_E2E_SHOTS) await page.screenshot({ path: join(process.env.IJ_E2E_SHOTS, "ij-harness-desktop.png") });
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
  await expect(page.getByTestId("pane-terminal")).toBeVisible();
  if (process.env.IJ_E2E_SHOTS) await page.screenshot({ path: join(process.env.IJ_E2E_SHOTS, "ij-harness-terminal-tab.png") });
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

test("a faked account limit shows the card; Resume when ready waits, survives a reload, and resumes", async ({ page }) => {
  test.skip(!sessionId, "needs the session from the first test");
  const calls: string[] = [];
  // Phase 5's routes, STUBBED in the browser with Phase 5's own shapes
  // (v1.368.0), whether or not the scratch daemon has Phase 5: the session's
  // account limit, its wait and the daemon's resume are faked; everything
  // else is the real daemon's.
  const WORK = {
    id: "work", name: "Work", vendor: "claude", vendor_label: "Claude Code", provider: "claude-cli",
    default: true, plan: "max", plan_label: "Max", status: "limit", resets_at: "2030-01-01T16:12:00Z", limit_kind: "usage",
  };
  const LIMIT = {
    account: "work", account_name: "Work", provider: "claude-cli", vendor: "claude",
    kind: "usage", resets_at: "2030-01-01T16:12:00Z", resumable: true, turn_id: "",
  };
  const RESUMED = "bt_resumed_e2e";
  let limited = false;
  let wait: Record<string, unknown> | null = null;
  // "" → the limit stands; "running" → the daemon resumed the session (the
  // reset came); "done" → the resumed turn finished and wrote its reply.
  let resumed: "" | "running" | "done" = "";
  await page.route(`${DAEMON}/build/accounts`, (route) =>
    stub(route, () => ({
      body: JSON.stringify({
        claude: [
          WORK,
          { ...WORK, id: "personal", name: "Personal", default: false, plan: "pro", plan_label: "Pro", status: "usage",
            usage: { percent: 38, window: "7 days", window_mins: 10080, resets_at: "2030-01-01T16:12:00Z" } },
        ],
        codex: [{ id: "cx", name: "Codex Plus", vendor: "codex", vendor_label: "Codex", provider: "codex-cli", default: true, plan: "plus", plan_label: "Plus", status: "ready" }],
        iron_proxy: { enabled: true, running: true, needs_update: false, note: "" },
      }),
    })),
  );
  // The session list as the real daemon answers it; while the faked resume
  // runs, this session reads as running (what the left pane polls).
  await page.route(`${DAEMON}/build/sessions`, async (route) => {
    if (route.request().method() === "OPTIONS") return stub(route, () => ({ body: "" }));
    const real = await route.fetch();
    const body = (await real.json()) as { folders: { sessions: { id: string; running?: boolean }[] }[] };
    for (const f of body.folders ?? []) {
      for (const s of f.sessions ?? []) if (s.id === sessionId && resumed === "running") s.running = true;
    }
    return stub(route, () => ({ body: JSON.stringify(body) }));
  });
  // The session as the real daemon answers it, with the faked account parts.
  const withAccount = async (route: Route) => {
    if (route.request().method() === "OPTIONS") return stub(route, () => ({ body: "" }));
    const real = await route.fetch({ url: `${DAEMON}/build/sessions/${sessionId}`, method: "GET" });
    const body = (await real.json()) as Record<string, unknown>;
    const messages = Array.isArray(body.messages) ? [...(body.messages as unknown[])] : [];
    if (resumed === "done") {
      messages.push({ role: "assistant", content: "Picked up where it stopped and finished.", turnId: RESUMED });
    }
    return stub(route, () => ({
      body: JSON.stringify({
        ...body,
        messages,
        running: resumed === "running",
        turn_id: resumed === "running" ? RESUMED : "",
        account: WORK,
        account_vendor: "claude",
        account_limit: limited && !resumed ? LIMIT : null,
        account_wait: resumed ? null : wait,
      }),
    }));
  };
  await page.route(`${DAEMON}/build/sessions/${sessionId}`, withAccount);
  await page.route(`${DAEMON}/build/sessions/${sessionId}/turn`, async (route) => {
    const frames = [
      ["round", { round: 0 }],
      ["account_limit", LIMIT],
      ["error", { detail: "parked", title: 'The Claude Code account "Work" is out of usage.', fix: "Your conversation and the work so far are kept." }],
    ]
      .map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`)
      .join("");
    // The daemon keeps the card on the session BEFORE its last frame goes
    // out (v1.369.0 review), so the page's re-read after `error` has it.
    if (route.request().method() !== "OPTIONS") {
      calls.push("turn");
      limited = true;
    }
    await stub(route, () => ({ contentType: "text/event-stream", body: frames }));
  });
  await page.route(`${DAEMON}/build/sessions/${sessionId}/resume-when-ready`, async (route) => {
    const method = route.request().method();
    if (method === "POST") wait = { account: "work", account_name: "Work", resets_at: "2030-01-01T16:12:00Z" };
    if (method === "DELETE") wait = null;
    if (method !== "OPTIONS") calls.push(method);
    return withAccount(route);
  });
  // The resumed turn the page rejoins by its name.
  await page.route(`${DAEMON}/chat/turns/${RESUMED}/stream`, async (route) => {
    const frames = [
      ["token", { text: "Picked up where it stopped and finished." }],
      ["done", { reply: "Picked up where it stopped and finished.", route: { provider: "claude-cli", model: "", account: { id: "work", name: "Work" } } }],
    ]
      .map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`)
      .join("");
    if (await stub(route, () => ({ contentType: "text/event-stream", body: frames }))) {
      calls.push("rejoin");
      resumed = "done";
    }
  });

  await page.goto(`/terminals?session=${sessionId}`);
  await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId("account-chip")).toContainText("Work");
  const box = page.getByTestId("harness-input");
  await box.fill("Keep going");
  await box.press("Enter");
  const card = page.getByTestId("account-limit-card");
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card).toContainText(
    "This account reached its limit. Wait for it to reset, or choose another account. Your conversation and work are preserved.",
  );
  await expect(page.getByTestId("account-limit-line")).toContainText("Work · resets");
  // The card says it once: the error frame's words are not repeated.
  await expect(page.getByText("is out of usage")).toHaveCount(0);
  await page.getByTestId("account-limit-resume").click();
  await expect(page.getByTestId("account-limit-waiting")).toBeVisible();
  // The armed wait survives a reload (the session view keeps it).
  await page.reload();
  await expect(page.getByTestId("account-limit-waiting")).toBeVisible({ timeout: 60_000 });
  if (process.env.IJ_E2E_SHOTS) await page.screenshot({ path: join(process.env.IJ_E2E_SHOTS, "ij-harness-limit.png") });
  await page.getByTestId("account-limit-cancel").click();
  await expect(page.getByTestId("account-limit-resume")).toBeVisible();

  // Armed again, and the reset comes: the daemon resumes the session on its
  // own. The left pane's poll sees it running, the session rejoins the turn
  // by its name, the card goes, and the reply is there.
  await page.getByTestId("account-limit-resume").click();
  await expect(page.getByTestId("account-limit-waiting")).toBeVisible();
  resumed = "running";
  await expect(page.getByTestId("harness-reply").last()).toContainText("Picked up where it stopped and finished.", {
    timeout: 30_000,
  });
  await expect(page.getByTestId("account-limit-card")).toHaveCount(0);
  expect(calls).toEqual(["turn", "POST", "DELETE", "POST", "rejoin"]);
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
    // v1.369.0 review: the conversation is ONE screen-sized unit — header,
    // transcript and composer — and the panes come after it. It used to flex
    // to its content's height, squeezing the transcript to a sliver.
    const box = await page.evaluate(() => {
      const r = (id: string) => document.querySelector(`[data-testid="${id}"]`)?.getBoundingClientRect() ?? null;
      const conv = r("harness-conversation");
      const composer = r("harness-composer");
      const tabs = r("harness-pane-tabs");
      return conv && composer && tabs
        ? { vh: window.innerHeight, top: conv.top, h: conv.height, composerBottom: composer.bottom, tabsTop: tabs.top }
        : null;
    });
    expect(box).not.toBeNull();
    expect(Math.abs(box!.h - box!.vh * 0.78)).toBeLessThanOrEqual(2);
    expect(box!.composerBottom).toBeLessThanOrEqual(box!.top + box!.h + 1);
    expect(box!.tabsTop).toBeGreaterThanOrEqual(box!.top + box!.h);
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
