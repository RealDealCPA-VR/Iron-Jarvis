import { expect, test, type BrowserContext, type Page } from "@playwright/test";

/**
 * Readiness 12B review (G05): a chat waiting for your OK is seen WHERE YOU
 * ARE — proven in the real browser (Edge), against the real daemon (the
 * scratch stack of playwright.config.ts), with its demo model.
 *
 * The chat page names itself on its stream (`X-IJ-Viewer`) and says when it
 * leaves the screen (`POST /chat/turns/{id}/away`); a custom header makes
 * every cross-origin request preflighted, so this file also proves the
 * daemon's CORS answer allows it and the browser caches that preflight.
 *
 * ONE NOTICE PER ASK (v1.360.0): `window.Notification` is a recorder here
 * (granted), so every desktop notice the bell would show is counted. The
 * card on the chat page IS the ask's notice while the user looks at it; the
 * bell pings only when the user is somewhere the card is not.
 *
 * The approval card: the composer's Permissions chip on "Ask first", and a
 * message that arms `write_file` — the demo model's one scripted step is a
 * `write_file`, which cards.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const WEB = `http://127.0.0.1:${process.env.IJ_E2E_WEB_PORT || 8808}`;
const composer = (page: Page) => page.getByRole("textbox", { name: "Message" });
const card = (page: Page) => page.getByTestId("dock-ask");

test.describe.configure({ mode: "serial" });

/** Every desktop notice this window shows, recorded (survives client-side
 *  navigation; a reload starts a fresh list). */
async function recordNotices(context: BrowserContext) {
  await context.addInitScript(() => {
    const w = window as unknown as { __notes: { title: string; body: string }[] };
    w.__notes = [];
    class RecordingNotification {
      static permission = "granted";
      static requestPermission() {
        return Promise.resolve("granted");
      }
      onclick: unknown = null;
      constructor(title: string, opts?: { body?: string }) {
        w.__notes.push({ title: String(title), body: String(opts?.body ?? "") });
      }
      close() {}
    }
    (window as unknown as { Notification: unknown }).Notification = RecordingNotification;
  });
}

async function chatNotices(page: Page): Promise<string[]> {
  const notes = await page.evaluate(
    () => (window as unknown as { __notes?: { title: string; body: string }[] }).__notes ?? [],
  );
  return notes.map((n) => `${n.title} | ${n.body}`).filter((s) => /chat/i.test(s));
}

async function pendingChats(page: Page): Promise<{ id: string; thread_id: string }[]> {
  const r = await page.request.get(`${DAEMON}/chat/approvals/pending`);
  expect(r.ok()).toBeTruthy();
  const body = (await r.json()) as { approvals: { kind: string; id: string; thread_id: string }[] };
  return body.approvals.filter((a) => a.kind === "chat");
}

/** A chat on `path` whose turn is parked on its approval card. */
async function park(page: Page, path = "/chat?new=1") {
  await page.goto(path);
  await expect(composer(page)).toBeVisible({ timeout: 120_000 });
  const chip = page.locator("#chat-approval-mode");
  if ((await chip.getAttribute("data-mode")) !== "always_ask") {
    await chip.click();
    await page.locator('[data-testid="permission-item"][data-mode="always_ask"]').click();
    await expect(chip).toHaveAttribute("data-mode", "always_ask");
  }
  await composer(page).fill("Write RESULT.md with a short hello note");
  await composer(page).press("Enter");
  await expect(card(page)).toBeVisible({ timeout: 90_000 });
}

async function settle(page: Page, ms = 2500) {
  await page.waitForTimeout(ms);
}

async function answer(page: Page) {
  // Deny: nothing is written, the turn ends.
  await card(page).locator("[data-dock-decline]").first().click();
  await expect(card(page)).toHaveCount(0, { timeout: 30_000 });
}

test.beforeEach(async ({ context }) => {
  await recordNotices(context);
});

test("the daemon allows the viewer header on its preflight, and the browser caches it", async ({ page }) => {
  // The daemon's own answer (the loopback CORS branch this stack runs).
  for (const path of ["/chat/stream", "/chat/turns/t-any/stream", "/chat/turns/t-any/away"]) {
    const r = await page.request.fetch(`${DAEMON}${path}`, {
      method: "OPTIONS",
      headers: {
        Origin: WEB,
        "Access-Control-Request-Method": path.endsWith("/stream") && path !== "/chat/stream" ? "GET" : "POST",
        "Access-Control-Request-Headers": "authorization,content-type,x-ij-viewer",
      },
    });
    expect(r.status(), path).toBe(200);
    const h = r.headers();
    expect(h["access-control-allow-origin"], path).toBe(WEB);
    expect((h["access-control-allow-headers"] || "").toLowerCase(), path).toContain("x-ij-viewer");
    expect(Number(h["access-control-max-age"] || 0), path).toBeGreaterThan(0);
  }

  // The browser: two turns on one page — the stream carries the header, and
  // its preflight goes out at most once (cached).
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Network.enable");
  const preflights: string[] = [];
  cdp.on("Network.requestWillBeSent", (e: { request: { method: string; url: string } }) => {
    if (e.request.method === "OPTIONS") preflights.push(new URL(e.request.url).pathname);
  });
  const streams: Record<string, string>[] = [];
  page.on("request", async (req) => {
    if (req.method() === "POST" && new URL(req.url()).pathname === "/chat/stream") {
      streams.push(await req.allHeaders());
    }
  });
  await page.goto("/chat?new=1");
  await expect(composer(page)).toBeVisible({ timeout: 120_000 });
  for (const text of ["Hello there", "And again"]) {
    await composer(page).fill(text);
    await composer(page).press("Enter");
    await expect(page.getByText(/This is the demo model/).last()).toBeVisible({ timeout: 90_000 });
    await expect(composer(page)).toBeEnabled();
    await page.waitForTimeout(500);
  }
  expect(streams.length).toBe(2);
  for (const h of streams) expect(h["x-ij-viewer"] || "").toMatch(/^view-/);
  expect(streams[0]["x-ij-viewer"]).toBe(streams[1]["x-ij-viewer"]); // one viewer per mounted page
  const streamPreflights = preflights.filter((p) => p === "/chat/stream");
  expect(streamPreflights.length, JSON.stringify(preflights)).toBeLessThanOrEqual(1);
});

test("on the chat page the card is the notice; on another page the bell lists it, once", async ({ page }) => {
  await park(page);
  await settle(page);
  expect(await pendingChats(page)).toEqual([]);
  expect(await chatNotices(page)).toEqual([]);

  // Another page of the SAME window (client-side navigation).
  await page.getByTestId("sidebar-nav-everything").click();
  await page.waitForURL((u) => u.pathname === "/everything");
  await expect.poll(async () => (await pendingChats(page)).length, { timeout: 20_000 }).toBe(1);
  await expect.poll(() => chatNotices(page), { timeout: 20_000 }).toHaveLength(1);
  await page.getByRole("button", { name: /notifications/i }).first().click();
  await expect(page.getByTestId("bell-chat-waiting")).toContainText("A chat is waiting for your OK");
  await settle(page, 3000);
  expect(await chatNotices(page)).toHaveLength(1); // once, not on every poll

  // Back to the chat from the bell: the card is in front of the user again,
  // so the daemon stops listing it — and no second notice.
  await page.getByTestId("bell-chat-waiting").getByRole("link").click();
  await expect(card(page)).toBeVisible({ timeout: 60_000 });
  await expect.poll(async () => (await pendingChats(page)).length, { timeout: 20_000 }).toBe(0);
  expect(await chatNotices(page)).toHaveLength(1);
  await answer(page);
});

test("a / ⇄ /chat remount never pings for the card that comes straight back", async ({ page }) => {
  await park(page);
  await settle(page);
  expect(await chatNotices(page)).toEqual([]);
  // A SLOW rejoin (a busy daemon, a cold route): the new page's read of the
  // turn it follows is held 2.5 s — longer than the page that left waits
  // before telling the bell — so the moment the daemon lists the chat (the
  // old page said "away", the new one has not followed yet) is certain.
  await page.route(/\/chat\/turns\?thread_id=/, async (route) => {
    await new Promise((r) => setTimeout(r, 2500));
    await route.continue();
  });
  // The bell reads the list on its own poll and on events — at ANY moment.
  // Make it read constantly while the chat page remounts, so a moment the
  // daemon lists the row (the old page said "away", the new one has not
  // rejoined yet) cannot hide behind a lucky poll.
  await page.evaluate(() => {
    (window as unknown as { __hammer: number }).__hammer = window.setInterval(
      () => window.dispatchEvent(new Event("ij:chat-asks-changed")),
      40,
    );
  });
  const seen: number[] = [];
  let polling = true;
  const poller = (async () => {
    while (polling) {
      seen.push((await pendingChats(page)).length);
      await page.waitForTimeout(30);
    }
  })();
  await page.getByRole("link", { name: "Iron Jarvis — home" }).click();
  await page.waitForURL((u) => u.pathname === "/");
  await expect(card(page)).toBeVisible({ timeout: 60_000 });
  await settle(page, 3000);
  polling = false;
  await poller;
  await page.evaluate(() => window.clearInterval((window as unknown as { __hammer: number }).__hammer));
  const listed = seen.filter((n) => n > 0).length;
  console.log(`remount: the daemon listed the chat in ${listed} of ${seen.length} reads`);
  expect(listed, "the held rejoin must open the gap this test is about").toBeGreaterThan(0);
  expect(await chatNotices(page)).toEqual([]);
  await page.unroute(/\/chat\/turns\?thread_id=/);
  await answer(page);
});

test("a reload of a chat parked on its card does not ping for it", async ({ page }) => {
  await park(page);
  await settle(page);
  await page.reload();
  await expect(card(page)).toBeVisible({ timeout: 90_000 });
  await settle(page, 4000);
  expect(await chatNotices(page)).toEqual([]);
  expect(await pendingChats(page)).toEqual([]);
  await answer(page);
});

test("two windows on one chat: the other still shows it; when it closes, the bell lists it", async ({ page, context }) => {
  await park(page);
  await settle(page);
  // The chat's id: the newest saved thread.
  const threads = (await (await page.request.get(`${DAEMON}/chat/threads`)).json()) as {
    threads: { id: string }[];
  };
  const thread = threads.threads[0].id;
  const other = await context.newPage();
  await other.goto(`/chat?thread=${encodeURIComponent(thread)}`);
  await expect(card(other)).toBeVisible({ timeout: 90_000 });

  await page.getByTestId("sidebar-nav-everything").click();
  await page.waitForURL((u) => u.pathname === "/everything");
  await settle(page, 4000);
  expect(await pendingChats(page)).toEqual([]); // the other window shows it
  expect(await chatNotices(page)).toEqual([]);
  expect(await chatNotices(other)).toEqual([]);

  // The other window closes (a closed tab): nobody shows the card now.
  await other.close();
  await expect.poll(async () => (await pendingChats(page)).length, { timeout: 20_000 }).toBe(1);
  // The bell here reads it on its own poll (no event reaches this window).
  await expect.poll(() => chatNotices(page), { timeout: 30_000 }).toHaveLength(1);

  await page.getByRole("button", { name: /notifications/i }).first().click();
  await page.getByTestId("bell-chat-waiting").getByRole("link").click();
  await expect(card(page)).toBeVisible({ timeout: 60_000 });
  await answer(page);
});
