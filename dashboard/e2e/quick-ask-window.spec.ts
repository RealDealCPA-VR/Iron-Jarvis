import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

/**
 * Quick Ask's answer window, LOOKED AT (next leaps 36-a, review): the chat
 * page drawn compact in a window the size of the desktop's answer window.
 *
 * The desktop opens `/chat?new=1&quick=1&compact=1&popout=1` in a 480 by 560
 * window (desktop/main.js `quickAnswerUrl`, `QUICK_ANSWER_WIDTH/HEIGHT`).
 * Here the same address opens in the real browser (Edge) against the scratch
 * daemon of playwright.config.ts (a fresh home, so replies are the demo
 * model's and the demo strip SHOWS: the least room the messages get), with
 * the desktop bridge stubbed as the preload gives it to that window: the
 * answer window's word (`quickAsk.isAnswerWindow`), the words waiting from the
 * box (`quickAsk.take`), and a pop-out's bridge (no sidebar, the "own window"
 * title bar). Measured at 480 by 560 and at a phone's 390 by 844, once the
 * conversation is longer than the window:
 *  - the page itself never scrolls or pans: only the transcript scrolls;
 *  - the composer is inside the window, at its bottom;
 *  - the transcript scrolls and keeps room to read: at least 120 px in this
 *    demo state (the strip above and the demo offer below the box both
 *    show; 133 px measured at 480 by 560, six lines), and at least 240 px
 *    once a real model answers, when both are gone and the empty row under
 *    the box takes no room (`room.everyday`: the transcript plus the strip
 *    plus that row, each measured here);
 *  - the demo reply's note points at "the button just below the message
 *    box": that offer IS below the box here too;
 *  - the Quick Ask header is on screen, under the app's title bar.
 * Not measured here: the native close button the desktop draws over the
 * title bar's right end (titleBarOverlay), which a browser has no env() for.
 * IJ_QUICK_OUT=<folder> also writes each case's measurements and a
 * screenshot.
 */

const QUICK_URL = "/chat?new=1&quick=1&compact=1&popout=1";
const FIRST_QUESTION = "In one line: what does Quick Ask do?";
/** A long message, so the conversation outgrows the small window fast. */
const LONG = Array.from({ length: 14 }, (_, i) => `Line ${i + 1} of a long note I want answered beside my document.`).join("\n");

async function stubAnswerWindow(page: Page) {
  await page.addInitScript((first) => {
    const w = window as unknown as {
      ironjarvis?: unknown;
      __quick?: { hides: number; toMain: unknown[]; taken: number };
    };
    const rec = { hides: 0, toMain: [] as unknown[], taken: 0 };
    w.__quick = rec;
    w.ironjarvis = {
      quickAsk: {
        // The box's words, taken ONCE (the real IPC hands them out once).
        take: async () => (rec.taken++ === 0 ? first : ""),
        isAnswerWindow: true,
        toMain: async (t: unknown) => {
          rec.toMain.push(t);
          return { ok: true };
        },
        hide: async () => {
          rec.hides += 1;
          return true;
        },
        onFocus: () => () => {},
      },
      popout: {
        isPopout: true,
        path: "/chat",
        open: async () => null,
        list: async () => [],
        focus: async () => null,
        close: async () => null,
      },
    };
  }, FIRST_QUESTION);
}

type Look = {
  vw: number;
  vh: number;
  docW: number;
  docH: number;
  strip: number;
  titleBar: { top: number; bottom: number } | null;
  header: { top: number; bottom: number } | null;
  composer: { top: number; bottom: number; left: number; right: number };
  meta: { top: number; bottom: number } | null;
  offer: { top: number; bottom: number } | null;
  transcript: { top: number; bottom: number; scrollH: number; clientH: number };
};

async function look(page: Page): Promise<Look> {
  return page.evaluate(() => {
    const rect = (sel: string) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left), right: Math.round(r.right) };
    };
    const doc = document.scrollingElement as HTMLElement;
    const sc = document.querySelector('[data-testid="chat-scroll"]') as HTMLElement;
    const scr = sc.getBoundingClientRect();
    const strip = document.querySelector('[data-testid="simulated-banner"]');
    return {
      vw: window.innerWidth,
      vh: window.innerHeight,
      docW: doc.scrollWidth,
      docH: doc.scrollHeight,
      strip: strip ? Math.round(strip.getBoundingClientRect().height) : 0,
      titleBar: rect("[data-ij-titlebar]"),
      header: rect('[data-testid="quick-ask-header"]'),
      composer: rect('[data-testid="chat-composer"]')!,
      meta: rect('[data-testid="composer-meta"]'),
      offer: rect('[data-testid="demo-connect"]'),
      transcript: {
        top: Math.round(scr.top),
        bottom: Math.round(scr.bottom),
        scrollH: sc.scrollHeight,
        clientH: sc.clientHeight,
      },
    };
  });
}

const composer = (page: Page) => page.getByRole("textbox", { name: "Message" });

/** The reply has ended: Send is back where Stop was. */
async function replyEnded(page: Page) {
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toHaveCount(0, { timeout: 90_000 });
}

async function sendAndWait(page: Page, text: string, lastLine: string) {
  await composer(page).fill(text);
  await composer(page).press("Enter");
  await expect(page.getByTestId("chat-scroll")).toContainText(lastLine, { timeout: 30_000 });
  await replyEnded(page);
}

for (const size of [
  { name: "the answer window, 480 by 560", width: 480, height: 560 },
  { name: "a phone, 390 by 844", width: 390, height: 844 },
]) {
  test(`Quick Ask's window keeps room to read: ${size.name}`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await stubAnswerWindow(page);
    await page.goto(QUICK_URL);
    await expect(page.getByTestId("quick-ask-header")).toBeVisible({ timeout: 120_000 });
    await expect(composer(page)).toBeVisible({ timeout: 120_000 });
    // The box's words were sent once and answered (the demo model).
    await expect(page.getByTestId("chat-scroll")).toContainText(FIRST_QUESTION, { timeout: 90_000 });
    await replyEnded(page);
    // Longer than the window.
    await sendAndWait(page, LONG, "Line 14 of a long note");
    await sendAndWait(page, LONG, "Line 14 of a long note");

    const m = await look(page);
    const demoRoom = m.transcript.bottom - m.transcript.top;
    // Once a real model answers, the strip and the demo offer are gone and
    // the row under the box is empty, so it takes no room (`empty:hidden`).
    const metaH = m.meta ? m.meta.bottom - m.meta.top : 0;
    const room = { demo: demoRoom, everyday: demoRoom + m.strip + metaH };
    const out = process.env.IJ_QUICK_OUT;
    if (out) {
      mkdirSync(out, { recursive: true });
      const slug = `${size.width}x${size.height}`;
      writeFileSync(join(out, `${slug}.json`), JSON.stringify({ ...m, room }, null, 2));
      await page.screenshot({ path: join(out, `${slug}.png`) });
    }

    // The demo strip shows (a fresh home): the least room the chat gets.
    expect.soft(m.strip, "the demo strip is on screen").toBeGreaterThan(0);
    // The page itself neither scrolls nor pans.
    expect.soft(m.docH, "the page does not scroll").toBeLessThanOrEqual(m.vh + 1);
    expect.soft(m.docW, "the page does not pan").toBeLessThanOrEqual(m.vw + 1);
    // The header is on screen, under the title bar.
    expect.soft(m.header, "the Quick Ask header").not.toBeNull();
    if (m.header && m.titleBar) expect.soft(m.header.top).toBeGreaterThanOrEqual(m.titleBar.bottom - 1);
    // The composer is inside the window, at its bottom.
    expect.soft(m.composer.bottom, "the composer ends inside the window").toBeLessThanOrEqual(m.vh);
    expect.soft(m.composer.bottom, "the composer is at the bottom").toBeGreaterThan(m.vh - 120);
    expect.soft(m.composer.left).toBeGreaterThanOrEqual(0);
    expect.soft(m.composer.right).toBeLessThanOrEqual(m.vw);
    // The demo note's "button just below the message box" is there.
    expect.soft(m.offer, "the demo offer").not.toBeNull();
    if (m.offer) expect.soft(m.offer.top, "the demo offer is below the box").toBeGreaterThanOrEqual(m.composer.bottom - 1);
    // The transcript keeps room to read, above the composer, and scrolls.
    expect.soft(room.demo, "room to read, demo strip and offer up").toBeGreaterThanOrEqual(120);
    expect.soft(room.everyday, "room to read once a real model answers").toBeGreaterThanOrEqual(240);
    expect.soft(m.transcript.bottom, "the transcript ends above the composer").toBeLessThanOrEqual(m.composer.top + 1);
    expect.soft(m.transcript.scrollH, "the conversation is longer than the room").toBeGreaterThan(m.transcript.clientH);
    const moved = await page.evaluate(() => {
      const sc = document.querySelector('[data-testid="chat-scroll"]') as HTMLElement;
      sc.scrollTop = 0;
      const top = sc.scrollTop;
      sc.scrollTop = sc.scrollHeight;
      return sc.scrollTop - top;
    });
    expect.soft(moved, "the transcript scrolls inside itself").toBeGreaterThan(0);
    // Esc puts the window away (the bridge's hide), and the reply is kept.
    await composer(page).press("Escape");
    expect.soft(await page.evaluate(() => (window as unknown as { __quick: { hides: number } }).__quick.hides)).toBe(1);
  });
}
