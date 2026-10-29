import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * IJ harness plan Phase 6 — the screen's done-when, in a real browser against
 * the scratch daemon (port 8807, a FRESH home whose model is the demo model):
 *
 *  1. open the chat page and switch to Code (v1.406.0; it was Build's IJ
 *     harness view);
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
 *  6. 390 px: Build never pans sideways;
 *  7. (v1.370.0, Phase 7) Rewind to here on the demo model: the first press
 *     says what goes back and touches nothing, the second removes the file
 *     the turn wrote, puts the message back in the box and drops the reply;
 *     the demo model leaves no library suggestion.
 *  8. (v1.373.0, part 2 Phase 2) at 390 px the "/" and "@" lists open above
 *     the box — never over it, never past the window — and Escape closes
 *     each one without sending; an "@" pick is a chip in the box;
 *  9. on the demo model, a folder skill picked with "/" and a file picked
 *     with "@" ride a real turn: the question the daemon saves names both.
 * 10. (v1.376.0) the model menu with Iron-Proxy accounts (stubbed): one
 *     group per company, one entry per account, a turned-off one not
 *     pickable (v1.377.0: a signed-out one IS pickable — the chip's rule and
 *     the daemon's); at 390 px (a sheet) and at 1280 px it stays in the
 *     window, and a pick under another account sends the account and the
 *     model in one request — both chips then name it.
 * 11. (v1.377.0, part 2 Phase 6) in the real browser with the app's real
 *     palette mounted: Ctrl+K in the harness opens the MODEL menu and never
 *     the palette; with the focus on the app's own controls it is the
 *     palette's; Ctrl+L puts the cursor in the box; Ctrl+N opens New
 *     session; the left pane's search narrows the list; Branch from here
 *     opens a new session holding the conversation up to that reply.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;

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
  // v1.406.0 (part 1): the coding sessions are the chat page's Code view.
  await page.goto("/chat");
  const tab = page.getByTestId("chat-view-code");
  await expect(tab).toBeVisible({ timeout: 90_000 });
  await tab.click();
  await expect(page.getByTestId("ij-harness-view")).toBeVisible({ timeout: 60_000 });
}

test("Chat › Code → a session → a reply with a change, and Undo", async ({ page }) => {
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

test("Rewind to here: the file goes back, the message returns to the box, the demo model leaves no lesson", async ({ page }) => {
  test.skip(!sessionId, "needs the session from the first test");
  // v1.370.0 (IJ harness Phase 7) on the demo model: its scripted step writes
  // RESULT.md, so a second message makes a turn with a file to rewind.
  await page.goto(`/terminals?session=${sessionId}`);
  await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
  const box = page.getByTestId("harness-input");
  // Wait for THIS turn's reply (v1.373.0 review): the first test's reply also
  // says "saved RESULT.md", so waiting on the last reply's words passed at
  // once, and the count read before the new reply landed (1, not 2). The
  // first test's reply is on screen before the count is read.
  await expect(page.getByTestId("harness-reply").first()).toBeVisible({ timeout: 30_000 });
  const before = await page.getByTestId("harness-reply").count();
  await box.fill("Write it once more");
  await box.press("Enter");
  await expect(page.getByTestId("harness-reply")).toHaveCount(before + 1, { timeout: 90_000 });
  await expect(page.getByTestId("harness-reply").last()).toContainText("saved RESULT.md", { timeout: 90_000 });
  await expect.poll(() => existsSync(join(work, "RESULT.md")), { timeout: 20_000 }).toBe(true);
  const replies = await page.getByTestId("harness-reply").count();

  // First press: what would go back, in words — nothing is touched yet.
  const bubble = page.getByTestId("harness-user-bubble").last();
  await bubble.hover();
  await page.getByTestId("rewind-start").last().click();
  const confirm = page.getByTestId("rewind-confirm");
  await expect(confirm).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("rewind-files")).toContainText("RESULT.md is removed");
  expect(existsSync(join(work, "RESULT.md"))).toBe(true);
  if (process.env.IJ_E2E_SHOTS) await page.screenshot({ path: join(process.env.IJ_E2E_SHOTS, "ij-harness-rewind.png") });

  // Second press: the file goes, the message is back in the box, the reply
  // it led to is gone from the conversation.
  await page.getByTestId("rewind-confirm-button").click();
  await expect.poll(() => existsSync(join(work, "RESULT.md")), { timeout: 20_000 }).toBe(false);
  await expect(box).toHaveValue("Write it once more", { timeout: 20_000 });
  await expect(page.getByTestId("harness-reply")).toHaveCount(replies - 1);
  // The demo model never writes a lesson (the library's honest-mock rule).
  await expect(page.getByTestId("library-cards")).toHaveCount(0);
  await box.fill("");
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
  // Build (v1.406.0: the terminals only), focused on THAT terminal.
  await expect(page).toHaveURL(new RegExp(`/terminals\\?focus=${termId}`));
  await expect(page.getByTestId("pane-rail")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId(`rail-row-${termId}`)).toHaveAttribute("data-active", "true");
  await expect(page.getByTestId(`rail-pane-${termId}`)).toHaveCSS("visibility", "visible");
  // …and back to the same session (Chat › Code remembers it).
  await page.goto("/chat?view=code");
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

// v1.376.0: the model menu groups by company and lists the Iron-Proxy
// accounts — STUBBED in the browser (GET /models, GET /build/accounts and
// the session's account fields, the PATCH), in the daemon's own shapes,
// so nothing about the scratch daemon's real session changes. At 390 px
// and at 1280 px the chip names the account inside the box (Send stays in
// it), the menu and an account's model panel stay inside the window (on a
// phone the menu is a sheet and the panel opens under its row; on a desktop
// beside the menu), and a pick under another account is ONE request
// carrying the account and the model.
async function modelMenuAccounts(page: Page, phone: boolean) {
  const acct = (over: Record<string, unknown>) => ({
    vendor: "claude", vendor_label: "Claude Code", provider: "claude-cli", default: false, plan: null, plan_label: "", ...over,
  });
  const WORK = acct({ id: "work", name: "Work", default: true, plan: "max", plan_label: "Max", status: "ready" });
  const PERSONAL = acct({
    id: "personal", name: "Personal", plan: "pro", plan_label: "Pro", status: "usage",
    usage: { percent: 38, window: "7 days", window_mins: 10080, resets_at: "2030-01-01T16:12:00Z" },
  });
  const SIDE = acct({ id: "side", name: "Side", plan: "pro", plan_label: "Pro", status: "needs_sign_in" });
  // v1.377.0: a TURNED-OFF account is the one the menu cannot pick (the
  // daemon refuses it); a signed-out one can be picked, as in the chip.
  const SHUT = acct({ id: "shut", name: "Shut", status: "off" });
  const models = [
    { provider: "claude-cli", model: "subscription", available: true, kind: "cli", inherited_from: null },
    { provider: "claude-cli", model: "claude-opus-4-8", label: "Opus 4.8", available: true, kind: "cli", inherited_from: null },
    { provider: "claude-cli", model: "claude-sonnet-4-6", label: "Sonnet 4.6", available: true, kind: "cli", inherited_from: null },
    { provider: "anthropic", model: "claude-opus-4-8", label: "Opus 4.8", available: true, kind: "api", inherited_from: "claude-cli" },
    { provider: "codex-cli", model: "gpt-5.5", available: true, kind: "cli", inherited_from: null },
    { provider: "openai", model: "gpt-5.5", available: true, kind: "api", inherited_from: null },
    { provider: "mock", model: "mock-1", available: true, kind: "local", on_this_network: true },
  ];
  let on: { account: Record<string, unknown>; model: string } = { account: WORK, model: "claude-opus-4-8" };
  const patched: unknown[] = [];
  await page.route(`${DAEMON}/models`, (route) => stub(route, () => ({ body: JSON.stringify({ models }) })));
  await page.route(`${DAEMON}/build/accounts`, (route) =>
    stub(route, () => ({
      body: JSON.stringify({
        claude: [WORK, PERSONAL, SIDE, SHUT],
        codex: [],
        iron_proxy: { enabled: true, running: true, needs_update: false, note: "" },
      }),
    })),
  );
  await page.route(`${DAEMON}/build/sessions/${sessionId}`, async (route) => {
    if (route.request().method() === "OPTIONS") return stub(route, () => ({ body: "" }));
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON() as { account?: string; model?: string };
      patched.push(body);
      if (body.account === "personal") on = { account: PERSONAL, model: body.model ?? "" };
    }
    const real = await route.fetch({ url: `${DAEMON}/build/sessions/${sessionId}`, method: "GET" });
    const view = (await real.json()) as Record<string, unknown>;
    return stub(route, () => ({
      body: JSON.stringify({
        ...view,
        setup: {
          ...((view.setup as Record<string, unknown>) ?? {}),
          provider: "claude-cli",
          model: on.model,
          account: on.account.id,
          account_name: on.account.name,
        },
        account: on.account,
        account_vendor: "claude",
        account_limit: null,
        account_wait: null,
      }),
    }));
  });
  await page.goto(`/terminals?session=${sessionId}`);
  await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
  const chip = page.getByTitle("Switch model");
  await expect(chip).toHaveText("Opus 4.8 · Work", { timeout: 30_000 });
  const inBox = async () =>
    page.evaluate(() => {
      const r = (el: Element | null) => (el ? el.getBoundingClientRect() : null);
      const card = r(document.querySelector('[data-testid="harness-composer"]'));
      const send = r(document.querySelector('[data-testid="harness-send"]'));
      const chipEl = r(document.querySelector('[title="Switch model"]'));
      return card && send && chipEl
        ? { card: [card.left, card.right], send: [send.left, send.right], chip: [chipEl.left, chipEl.right] }
        : null;
    });
  const box = await inBox();
  expect(box).not.toBeNull();
  // The chip and Send are both inside the composer card.
  expect(box!.send[0]).toBeGreaterThanOrEqual(box!.card[0]);
  expect(box!.send[1]).toBeLessThanOrEqual(box!.card[1] + 0.5);
  expect(box!.chip[0]).toBeGreaterThanOrEqual(box!.card[0]);
  expect(box!.chip[1]).toBeLessThanOrEqual(box!.card[1] + 0.5);

  await chip.click();
  const menu = page.getByTestId("model-menu");
  await expect(menu.getByTestId("model-account-personal")).toBeVisible({ timeout: 20_000 });
  await expect(menu.locator('[data-company="claude"]')).toContainText("Claude (Anthropic)");
  await expect(menu.getByTestId("model-account-side")).toBeEnabled();
  await expect(menu.getByTestId("model-account-shut")).toBeDisabled();
  await expect(menu.getByTestId("model-account-work")).toHaveAttribute("data-current", "true");
  // Review: Escape on a row (a phone's sheet is all rows) closes the menu
  // and hands focus back to the chip; the chip opens it again.
  await menu.getByTestId("model-account-personal").focus();
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(chip).toBeFocused();
  await chip.click();
  await expect(menu.getByTestId("model-account-personal")).toBeVisible();
  await menu.getByTestId("model-account-personal").click();
  const panel = page.getByTestId("model-sub");
  await expect(panel).toBeVisible();
  const fit = await page.evaluate(() => {
    const r = (id: string) => document.querySelector(`[data-testid="${id}"]`)?.getBoundingClientRect() ?? null;
    const m = r("model-menu");
    const p = r("model-sub");
    return m && p
      ? {
          w: window.innerWidth,
          pan: document.documentElement.scrollWidth - window.innerWidth,
          menu: { left: m.left, right: m.right, top: m.top },
          panel: { left: p.left, right: p.right, top: p.top, height: p.height },
        }
      : null;
  });
  expect(fit).not.toBeNull();
  expect(fit!.menu.left).toBeGreaterThanOrEqual(0);
  expect(fit!.menu.right).toBeLessThanOrEqual(fit!.w);
  expect(fit!.menu.top).toBeGreaterThanOrEqual(0);
  expect(fit!.panel.left).toBeGreaterThanOrEqual(0);
  expect(fit!.panel.right).toBeLessThanOrEqual(fit!.w);
  expect(fit!.panel.height).toBeGreaterThan(40);
  expect(fit!.pan).toBeLessThanOrEqual(0);
  if (phone) {
    // A sheet: the panel opens UNDER its row, inside the menu.
    expect(fit!.panel.left).toBeGreaterThanOrEqual(fit!.menu.left);
    expect(fit!.panel.right).toBeLessThanOrEqual(fit!.menu.right + 0.5);
  } else {
    // Beside the menu, on its left, as the chat's (its edge sits on the
    // menu's 1 px border: 4 px from the row, which is 5 px inside).
    expect(fit!.panel.left).toBeLessThan(fit!.menu.left);
    expect(fit!.panel.right).toBeLessThanOrEqual(fit!.menu.left + 2);
  }
  if (process.env.IJ_E2E_SHOTS) {
    await page.screenshot({ path: join(process.env.IJ_E2E_SHOTS, `ij-harness-${phone ? "phone" : "desktop"}-model-accounts.png`) });
  }
  await panel.getByText("Sonnet 4.6").click();
  await expect(chip).toHaveText("Sonnet 4.6 · Personal", { timeout: 20_000 });
  await expect(page.getByTestId("account-chip-name")).toHaveText("Personal");
  expect(patched).toEqual([{ account: "personal", model: "claude-sonnet-4-6" }]);
  const after = await inBox();
  expect(after!.send[1]).toBeLessThanOrEqual(after!.card[1] + 0.5);
}

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
  // v1.376.0 (part 2, Phase 5): the Browser pane says where the agent's own
  // browser is and shows its latest picture — at 390 px with no sideways
  // pan, however long the address. The view and the picture are STUBBED in
  // the browser (the demo model opens no page), in the route's own shapes.
  test("the Browser pane shows the agent's view and fits the width", async ({ page }) => {
    test.skip(!sessionId, "needs the session from the first test");
    const picture = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
      "base64",
    );
    const where = "localhost:5173/" + "a-very-long-path-segment/".repeat(6) + "cart";
    await page.route(`${DAEMON}/build/sessions/${sessionId}/browser`, (route) =>
      stub(route, () => ({
        body: JSON.stringify({
          view: {
            open: true, where, blank: false, title: "Cart", at: new Date().toISOString(),
            shot: true, shot_at: "2026-10-23T09:41:00+00:00", note: "",
          },
        }),
      })),
    );
    await page.route(new RegExp(`/build/sessions/${sessionId}/browser/shot`), async (route) => {
      if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: CORS });
      await route.fulfill({ status: 200, headers: { ...CORS, "content-type": "image/png" }, body: picture });
    });
    await page.goto(`/terminals?session=${sessionId}`);
    await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
    await page.getByTestId("harness-pane-tab-browser").click();
    await expect(page.getByTestId("agent-browser-where")).toContainText("The agent is on localhost:5173/");
    await expect(page.getByTestId("agent-browser-shot")).toBeVisible();
    await page.waitForTimeout(500);
    const pan = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(pan).toBeLessThanOrEqual(0);
    const fits = await page.evaluate(() => {
      const r = (id: string) => document.querySelector(`[data-testid="${id}"]`)?.getBoundingClientRect() ?? null;
      const img = r("agent-browser-shot");
      const line = r("agent-browser-where");
      return img && line ? { right: Math.max(img.right, line.right), vw: window.innerWidth } : null;
    });
    expect(fits).not.toBeNull();
    expect(fits!.right).toBeLessThanOrEqual(fits!.vw);
    const shots = process.env.IJ_E2E_SHOTS;
    if (shots) await page.screenshot({ path: join(shots, "ij-harness-agent-browser-phone.png"), fullPage: true });
  });
  // v1.390.0 (the long-jobs plan's Phase 1): the Timeline tab draws the demo
  // model's real reply (its model call, its step) on one time axis — at
  // 390 px nothing pans sideways, every bar is inside the window, and the
  // rows scroll inside the tab.
  test("the Timeline tab draws the reply and fits the width", async ({ page }) => {
    test.skip(!sessionId, "needs the session from the first test");
    await page.goto(`/terminals?session=${sessionId}`);
    await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
    await page.getByTestId("harness-pane-tab-timeline").click();
    await expect(page.getByTestId("pane-timeline")).toBeVisible();
    await expect(page.getByTestId("timeline-totals")).toContainText("model call", { timeout: 20_000 });
    // The model you talk to, named as the composer names it.
    await expect(page.getByTestId("timeline-row").first()).toContainText("Demo model (scripted)");
    await page.waitForTimeout(500);
    const pan = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(pan).toBeLessThanOrEqual(0);
    const fits = await page.evaluate(() => {
      const vw = window.innerWidth;
      const all = [...document.querySelectorAll('[data-testid="timeline-bar"], [data-testid="timeline-segment"], [data-testid="timeline-row"]')];
      const rows = document.querySelector('[data-testid="timeline-rows"]');
      return {
        vw,
        right: Math.max(...all.map((el) => el.getBoundingClientRect().right)),
        left: Math.min(...all.map((el) => el.getBoundingClientRect().left)),
        scrollsInside: rows ? getComputedStyle(rows).overflowY : "",
        noSideways: rows ? getComputedStyle(rows).overflowX : "",
      };
    });
    expect(fits.right).toBeLessThanOrEqual(fits.vw);
    expect(fits.left).toBeGreaterThanOrEqual(0);
    expect(fits.scrollsInside).toBe("auto");
    expect(fits.noSideways).toBe("hidden");
    // A press opens the card, inside the window too.
    await page.getByTestId("timeline-bar").first().click();
    const card = page.getByTestId("timeline-card");
    await expect(card).toBeVisible();
    const cardBox = await card.boundingBox();
    expect(cardBox!.x + cardBox!.width).toBeLessThanOrEqual(390);
    const after = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(after).toBeLessThanOrEqual(0);
    const shots = process.env.IJ_E2E_SHOTS;
    if (shots) await page.screenshot({ path: join(shots, "ij-harness-timeline-phone.png"), fullPage: true });
    // And beside the conversation on a desktop window (the tab is in the
    // narrow column there): nothing pans either.
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.waitForTimeout(500);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    if (shots) await page.screenshot({ path: join(shots, "ij-harness-timeline-desktop.png") });
  });
  // v1.373.0 (part 2, Phase 2): the "/" and "@" lists open ABOVE the box —
  // never over it, never past the window's top or sides — and Escape closes
  // each one without sending or touching the words.
  test("the / and @ lists sit above the box and inside the window", async ({ page }) => {
    test.skip(!sessionId, "needs the session from the first test");
    writeFileSync(join(work, "notes-for-at.md"), "the build word is periwinkle\n");
    await page.goto(`/terminals?session=${sessionId}`);
    await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
    const box = page.getByTestId("harness-input");
    for (const [word, menu, row] of [
      ["/", "harness-slash-menu", "harness-slash-command"],
      ["@notes", "harness-at-menu", "harness-at-file"],
    ] as const) {
      await box.fill(word);
      await expect(page.getByTestId(menu)).toBeVisible({ timeout: 20_000 });
      await expect(page.getByTestId(row).first()).toBeVisible({ timeout: 20_000 });
      await page.waitForTimeout(300);
      const r = await page.evaluate((id) => {
        const rect = (el: Element | null) => (el ? el.getBoundingClientRect() : null);
        const m = rect(document.querySelector(`[data-testid="${id}"]`));
        const card = rect(document.querySelector('[data-testid="harness-composer"]'));
        const input = rect(document.querySelector('[data-testid="harness-input"]'));
        return m && card && input
          ? {
              w: window.innerWidth,
              h: window.innerHeight,
              pan: document.documentElement.scrollWidth - window.innerWidth,
              m: { top: m.top, bottom: m.bottom, left: m.left, right: m.right, height: m.height },
              cardTop: card.top,
              inputTop: input.top,
            }
          : null;
      }, menu);
      expect(r).not.toBeNull();
      // Above the box: its bottom edge at or above the card's top, so it
      // covers neither the card nor the box inside it.
      expect(r!.m.bottom).toBeLessThanOrEqual(r!.cardTop + 0.5);
      expect(r!.m.bottom).toBeLessThanOrEqual(r!.inputTop);
      // Inside the window, with rows to show.
      expect(r!.m.top).toBeGreaterThanOrEqual(0);
      expect(r!.m.left).toBeGreaterThanOrEqual(0);
      expect(r!.m.right).toBeLessThanOrEqual(r!.w);
      expect(r!.m.height).toBeGreaterThan(40);
      expect(r!.pan).toBeLessThanOrEqual(0);
      if (process.env.IJ_E2E_SHOTS) {
        await page.screenshot({ path: join(process.env.IJ_E2E_SHOTS, `ij-harness-phone-${menu}.png`) });
      }
      // Escape closes it; nothing is sent and the words stay.
      await box.press("Escape");
      await expect(page.getByTestId(menu)).toHaveCount(0);
      await expect(box).toHaveValue(word);
    }
    // A file picked with "@" becomes a chip in the box. (New words: typing
    // is what brings an Escaped list back, and the same words are no typing.)
    await box.fill("@notes-for");
    await expect(page.getByTestId("harness-at-file").first()).toBeVisible({ timeout: 20_000 });
    await box.press("Enter");
    await expect(page.getByTestId("harness-at-chip")).toContainText("notes-for-at.md");
    await expect(box).toHaveValue("");
    await page.getByRole("button", { name: "Remove notes-for-at.md" }).click();
    await expect(page.getByTestId("harness-at-chip")).toHaveCount(0);
  });
  test("the model menu lists the accounts, fits, and switches account and model in one press", async ({ page }) => {
    test.skip(!sessionId, "needs the session from the first test");
    await modelMenuAccounts(page, true);
  });
  test("Build (the terminals) fits the width", async ({ page }) => {
    await page.goto("/terminals");
    await expect(page.getByTestId("header-new-terminal")).toBeVisible({ timeout: 90_000 });
    await page.waitForTimeout(800);
    const pan = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(pan).toBeLessThanOrEqual(0);
  });
});

test.describe("1280 px — the model menu with accounts", () => {
  test.use({ viewport: { width: 1280, height: 900 } });
  test("the accounts list in the menu, the panel opens beside it, and a pick switches both", async ({ page }) => {
    test.skip(!sessionId, "needs the session from the first test");
    await modelMenuAccounts(page, false);
  });
});

// v1.370.2's "the Build switch is centred and fixed" moved ON PURPOSE in
// v1.406.0 (part 1): Build has no switch; the chat page's Chat | Code switch
// is pinned the same way in chat-code-switch.spec.ts.

// v1.373.0 (part 2, Phase 2) on the demo model: a skill of the folder's own
// harness layer picked with "/" and a file picked with "@" ride a real turn
// through the daemon — the question it saves names both.
test("/ and @ ride a real turn: the saved question names the folder skill and the file", async ({ page }) => {
  test.skip(!sessionId, "needs the session from the first test");
  writeFileSync(join(work, "notes-for-at.md"), "the build word is periwinkle\n");
  const skillDir = join(work, ".ironjarvis", "harness", "skills", "e2e-notes");
  mkdirSync(skillDir, { recursive: true });
  writeFileSync(join(skillDir, "SKILL.md"), "---\ndescription: answer from the notes file\n---\nRead the notes first.\n");
  await page.goto(`/terminals?session=${sessionId}`);
  await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
  const box = page.getByTestId("harness-input");
  await box.fill("/e2e");
  const skillRow = page.locator('[data-testid="harness-slash-skill"][data-name="e2e-notes"]');
  await expect(skillRow).toBeVisible({ timeout: 20_000 });
  await expect(skillRow).toHaveAttribute("data-source", "folder");
  await box.press("Enter");
  await expect(page.getByTestId("harness-skill-chip")).toContainText("e2e-notes");
  await box.fill("@notes-for");
  await expect(page.getByTestId("harness-at-file").first()).toBeVisible({ timeout: 20_000 });
  await box.press("Enter");
  await expect(page.getByTestId("harness-at-chip")).toContainText("notes-for-at.md");
  const replies = await page.getByTestId("harness-reply").count();
  await box.fill("What is the build word?");
  await box.press("Enter");
  await expect(page.getByTestId("harness-reply")).toHaveCount(replies + 1, { timeout: 90_000 });
  // The re-read transcript (the line the DAEMON wrote) names both.
  await expect(page.getByTestId("harness-sent-skill").last()).toContainText("e2e-notes", { timeout: 20_000 });
  await expect(page.getByTestId("harness-sent-file").last()).toContainText("notes-for-at.md");
  await expect(page.getByTestId("harness-skill-chip")).toHaveCount(0);
  await expect(page.getByTestId("harness-at-chip")).toHaveCount(0);
});

// v1.377.0 (part 2, Phase 6) in the real browser, with the app's own command
// palette mounted: the harness keys work INSIDE the IJ harness only, the left
// pane's search narrows the list, and Branch from here opens a new session.
test("Phase 6: the keys inside the harness only, the search, and Branch from here", async ({ page }) => {
  test.skip(!sessionId, "needs the session from the first test");
  await page.goto(`/terminals?session=${sessionId}`);
  await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId("harness-reply").first()).toBeVisible({ timeout: 30_000 });
  const box = page.getByTestId("harness-input");
  const palette = page.getByPlaceholder("Search anything — pages, skills, chats, or just ask…");

  // Ctrl+K in the box: the MODEL menu, never the palette.
  await box.click();
  await page.keyboard.press("Control+k");
  await expect(page.getByTestId("model-filter")).toBeVisible({ timeout: 10_000 });
  await expect(palette).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("model-filter")).toHaveCount(0);

  // Ctrl+K with the focus on the app's own control (the title bar's search):
  // the palette's, as everywhere else in the app.
  await page.getByLabel("Search Iron Jarvis (Ctrl K)").focus();
  await page.keyboard.press("Control+k");
  await expect(palette).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("model-filter")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(palette).toHaveCount(0);

  // Ctrl+L from the session's title: the cursor goes to the box.
  await page.getByTestId("harness-title").focus();
  await page.keyboard.press("Control+l");
  await expect(box).toBeFocused();

  // The search narrows the list by the session's title; Escape clears it.
  const search = page.getByTestId("harness-session-search");
  await expect(search).toBeVisible();
  await search.fill("zzz-no-such-session");
  await expect(page.getByTestId("harness-no-session-match")).toBeVisible();
  await search.press("Escape");
  await expect(page.getByTestId(`harness-session-row-${sessionId}`)).toBeVisible();

  // Branch from the first reply: a NEW session opens with the conversation up
  // to it; this session is untouched.
  const before = await page.getByTestId("harness-reply").count();
  const first = page.getByTestId("harness-reply").first();
  await first.hover();
  await first.getByTestId("harness-branch-reply").click();
  await expect(page.getByTestId("harness-branched-from")).toBeVisible({ timeout: 30_000 });
  const branched = (await page.getByTestId("harness-session").getAttribute("data-session")) ?? "";
  expect(branched).not.toBe(sessionId);
  await expect(page.getByTestId("harness-reply")).toHaveCount(1);
  await expect(page).toHaveURL(new RegExp(`session=${branched}`));
  // Back to the original from the "Branched from" line: all its replies.
  await page.getByTestId("harness-branched-from").click();
  await expect(page.getByTestId("harness-session")).toHaveAttribute("data-session", sessionId, { timeout: 30_000 });
  await expect(page.getByTestId("harness-reply")).toHaveCount(before, { timeout: 30_000 });

  // Ctrl+N with the focus in the harness: New session opens, ready to type.
  await box.click();
  await page.keyboard.press("Control+n");
  await expect(page.getByTestId("harness-new-session-form")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("harness-new-folder")).toBeFocused();
});

// v1.379.0: a typed "/name words" runs like Claude Code's "/": a skill rides
// that message with the words as its text, a custom command (one in the
// folder's .claude/commands) is expanded by the daemon and its question shows
// what was typed; an unknown "/word" is ordinary text.
test("v1.379.0: a typed /skill words and a typed /command words ride real turns", async ({ page }) => {
  test.skip(!sessionId, "needs the session from the first test");
  const cmdDir = join(work, ".claude", "commands");
  mkdirSync(cmdDir, { recursive: true });
  writeFileSync(
    join(cmdDir, "e2e-review.md"),
    "---\ndescription: Review a file for the e2e\nargument-hint: <file>\n---\nReview $ARGUMENTS and say one thing.\n",
  );
  await page.goto(`/terminals?session=${sessionId}`);
  await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId("harness-reply").first()).toBeVisible({ timeout: 30_000 });
  const box = page.getByTestId("harness-input");

  // The menu lists the folder's command with its source and hint.
  await box.fill("/e2e-rev");
  const row = page.locator('[data-testid="harness-slash-custom"][data-name="e2e-review"]');
  await expect(row).toBeVisible({ timeout: 20_000 });
  await expect(row).toContainText("this folder");
  await expect(row).toContainText("<file>");

  // A typed skill with words.
  let replies = await page.getByTestId("harness-reply").count();
  await box.fill("/research what otters eat");
  await expect(page.getByTestId("harness-slash-invoking")).toHaveAttribute("data-name", "research", { timeout: 20_000 });
  await box.press("Enter");
  await expect(page.getByTestId("harness-reply")).toHaveCount(replies + 1, { timeout: 90_000 });
  await expect(page.getByTestId("harness-sent-skill").last()).toContainText("research", { timeout: 20_000 });
  await expect(page.getByTestId("harness-user-bubble").last()).toHaveText("what otters eat");

  // A typed custom command with words: the question shows what was typed.
  replies = await page.getByTestId("harness-reply").count();
  await box.fill("/e2e-review notes-for-at.md");
  await expect(page.getByTestId("harness-slash-invoking")).toHaveAttribute("data-name", "e2e-review", { timeout: 20_000 });
  await box.press("Enter");
  await expect(page.getByTestId("harness-reply")).toHaveCount(replies + 1, { timeout: 90_000 });
  await expect(page.getByTestId("harness-user-bubble").last()).toHaveText("/e2e-review notes-for-at.md", {
    timeout: 20_000,
  });

  // An unknown "/word" is ordinary text.
  replies = await page.getByTestId("harness-reply").count();
  await box.fill("/no-such-command hello");
  await box.press("Enter");
  await expect(page.getByTestId("harness-reply")).toHaveCount(replies + 1, { timeout: 90_000 });
  await expect(page.getByTestId("harness-user-bubble").last()).toHaveText("/no-such-command hello");
});

// v1.379.0: a session with NO folder. The chat files folder is pointed at a
// scratch folder first, so nothing is made in the user's Documents.
test("v1.379.0: a session with no folder makes its own folder with its first message", async ({ page }) => {
  const chatFiles = mkdtempSync(join(tmpdir(), "ij-harness-e2e-chatfiles-"));
  try {
    const put = await page.request.put(`${DAEMON}/settings`, { data: { values: { chat_files_root: chatFiles } } });
    expect(put.ok()).toBe(true);
    await openHarness(page);
    await page.getByTestId("harness-new-session").click();
    const form = page.getByTestId("harness-new-session-form");
    await expect(form.getByTestId("harness-new-no-folder-line")).toContainText(
      `No folder: the session keeps its files in a new folder in ${chatFiles}`,
    );
    await form.getByTestId("harness-new-no-folder").click();
    await expect(page.getByTestId("harness-session")).toBeVisible();
    const folderLine = page.getByTestId("harness-folder");
    await expect(folderLine).toHaveAttribute("data-no-folder", "true");
    await expect(folderLine).toContainText("its files get a folder of their own with your first message");
    // Nothing made yet.
    expect(readdirSync(chatFiles)).toEqual([]);
    // The list's "No folder" group holds it.
    await expect(page.locator('[data-testid="harness-folder-group"][data-no-folder="true"]')).toBeVisible();

    const box = page.getByTestId("harness-input");
    await box.fill("Write a hello file");
    await box.press("Enter");
    await expect(page.getByTestId("harness-reply").last()).toContainText("saved RESULT.md", { timeout: 90_000 });
    await expect(page.getByTestId("harness-work-folder")).toContainText("files in ", { timeout: 30_000 });
    await expect(page.getByTestId("harness-open-work-folder")).toBeVisible();
    const made = readdirSync(chatFiles);
    expect(made).toHaveLength(1);
    expect(made[0]).toMatch(/^\d{4}-\d{2}-\d{2} Write a hello file$/);
    expect(existsSync(join(chatFiles, made[0], "RESULT.md"))).toBe(true);
    // No branch line: there is no repository.
    await expect(page.getByTestId("harness-branch")).toHaveCount(0);
  } finally {
    await page.request
      .put(`${DAEMON}/settings`, { data: { values: { chat_files_root: "" } } })
      .catch(() => undefined);
    try {
      rmSync(chatFiles, { recursive: true, force: true });
    } catch {
      /* left for the OS to clear */
    }
  }
});
