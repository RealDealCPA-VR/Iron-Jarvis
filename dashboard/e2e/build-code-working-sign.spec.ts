import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Build › Code's "working" sign in a real browser (the owner: the one plain
 * line "5 helpers are still working in the background…" was too easy to
 * miss; they asked for a small, calm visual in its place).
 *
 * The Code view's own routes (`/build/app/…`) are STUBBED in the browser with
 * the shapes the real daemon answers; nothing starts a coding tool. What a
 * unit test cannot see is checked here, at 1900, 1280 and 390 px wide, on a
 * dark and a light theme:
 *   - the sign is on screen, inside the window, and nothing pans sideways;
 *   - NOTHING MOVES when it goes: the message box and the last line of the
 *     conversation are where they were (its slot is always there);
 *   - the light moves in STEPS (a handful of places, never in between);
 *   - a reader who scrolled up still sees it, just above the message box,
 *     with a pill behind it;
 *   - with "reduce motion" on, nothing is animated and the words still show.
 *
 * `IJ_SIGN_OUT=<folder>` also writes a screenshot of each case.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const OUT = process.env.IJ_SIGN_OUT || "";

// Pictures for a person to look at are taken at twice the size (the checks
// below measure in CSS pixels either way).
test.use({ deviceScaleFactor: OUT ? 2 : 1 });

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
  id: "app_sign",
  harness: "claude-code",
  folder: "C:\\Users\\me\\Projects\\shop",
  title: "Check every report page and fix what is broken",
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "h1",
  created_at: "2026-11-10T09:00:00Z",
  updated_at: "2026-11-10T09:00:00Z",
  archived_at: null,
  last_error: "",
  running: false,
  process: true,
  waiting: false,
  background: 0,
  background_helpers: 0,
  self_reply: false,
};

const WORK = [
  "read the report pages and listed the ones that share a header",
  "checked the totals on the March statements against the ledger",
  "wrote the import step and its tests, then ran them",
  "looked for every place the old date format is still used",
  "fixed the rounding in the cash forecast and ran its tests",
];

function events(notices: number): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [
    { type: "user", text: "check every report page with a team and fix what is broken", turn: "t1", at: "2026-11-10T09:00:00Z" },
    {
      type: "text_done",
      item: "m1:0",
      text: "Five helpers are on it, one per area. I'll report as they finish.",
      turn: "t1",
      at: "2026-11-10T09:00:05Z",
    },
    { type: "done", stop: "completed", turn: "t1", at: "2026-11-10T09:00:06Z" },
  ];
  for (let i = 0; i < notices; i++) {
    out.push({ type: "notice", text: `A helper finished: ${WORK[i % WORK.length]}`, at: "2026-11-10T09:01:00Z" });
  }
  return out;
}

interface Stage {
  session: Record<string, unknown>;
  events: Record<string, unknown>[];
  running: string | null;
}

/** What the daemon answers for the session right now (a test changes it). */
async function stub(page: Page, stage: { now: Stage }): Promise<void> {
  await page.route(`${DAEMON}/build/app/harnesses`, (r) => json(r, TOOLS));
  await page.route(
    (url) => url.href.startsWith(`${DAEMON}/build/app/sessions`),
    (r) => {
      const path = new URL(r.request().url()).pathname;
      const s = stage.now;
      const session = { ...SESSION, ...s.session };
      if (path === "/build/app/sessions") return json(r, { sessions: [session] });
      if (path.endsWith("/commands")) return json(r, { commands: [] });
      if (path.endsWith("/changes")) return json(r, { source: "none", words: "", changes: [], files: 0 });
      if (path.endsWith("/usage")) return json(r, { harness: "claude-code", account: null, seen: null, totals: null, context: null });
      if (path.endsWith("/overview")) return json(r, { folder: SESSION.folder, folder_exists: true, on_share: false });
      return json(r, { session, events: s.events, running: s.running, turn_id: s.running, pending: [] });
    },
  );
}

const helpers = (n: number, notices = 2): Stage => ({
  session: { background: n, background_helpers: n },
  events: events(notices),
  running: null,
});
const quiet = (notices = 2): Stage => ({ session: {}, events: events(notices), running: null });

async function open(page: Page, theme: "dark" | "light"): Promise<void> {
  if (process.env.IJ_SIGN_DEBUG) {
    page.on("console", (m) => {
      if (m.type() === "error" || m.type() === "warning") console.log(`[page ${m.type()}] ${m.text().slice(0, 600)}`);
    });
    page.on("pageerror", (e) => console.log(`[page error] ${String(e).slice(0, 600)}`));
  }
  if (theme === "light") {
    await page.addInitScript(() => {
      try {
        localStorage.setItem("ij_theme", "mark1");
      } catch {
        /* a page with no storage keeps the default */
      }
    });
  }
  await page.goto(`/terminals?view=code&code=${SESSION.id}`);
  await expect(page.getByTestId("code-box")).toBeVisible({ timeout: 90_000 });
}

async function shot(page: Page, name: string): Promise<void> {
  if (!OUT) return;
  mkdirSync(OUT, { recursive: true });
  // The dev server's own badge (bottom left) is not part of the app.
  await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
  await page.screenshot({ path: join(OUT, `${name}.png`) });
  // …and the place where the sign sits, close up.
  const pane = await page.getByTestId("code-session").boundingBox();
  const slot = await page.getByTestId("code-sign-slot").boundingBox();
  if (!pane || !slot) return;
  const top = Math.max(pane.y, slot.y - 130);
  const bottom = Math.min(pane.y + pane.height, slot.y + slot.height + 130);
  const left = Math.max(pane.x, slot.x - 24);
  await page.screenshot({
    path: join(OUT, `${name}-close.png`),
    clip: { x: left, y: top, width: Math.min(560, pane.x + pane.width - left), height: bottom - top },
  });
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
      if (el.closest("pre")) continue;
      worst = Math.max(worst, r.right - w);
    }
    return Math.max(worst, document.documentElement.scrollWidth - w);
  });
}

/** Where the message box and the conversation's last row are. */
async function places(page: Page): Promise<{ box: number; last: number; scroll: number }> {
  return page.evaluate(() => {
    const box = document.querySelector<HTMLElement>("[data-testid='code-composer']")!.getBoundingClientRect().top;
    const rows = document.querySelector<HTMLElement>("[data-testid='code-conversation']")!;
    const last = (rows.lastElementChild as HTMLElement).getBoundingClientRect().bottom;
    const scroll = document.querySelector<HTMLElement>("[data-testid='code-transcript']")!.scrollHeight;
    return { box, last, scroll };
  });
}

for (const size of [
  { width: 1900, height: 1000 },
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  for (const theme of ["dark", "light"] as const) {
    test(`${size.width} px, ${theme}: helpers at work show in the ring, a finished one is counted, and nothing moves when it goes`, async ({
      page,
    }) => {
      await page.setViewportSize(size);
      const stage = { now: helpers(5) };
      await stub(page, stage);
      await open(page, theme);

      const sign = page.getByTestId("code-working-sign");
      await expect(sign).toBeVisible();
      await expect(sign).toHaveAttribute("data-sign", "helpers");
      await expect(page.getByTestId("code-sign-primary")).toHaveText("5 helpers working");
      await expect(page.getByTestId("code-sign-secondary")).toHaveText("their report will appear here");
      // The sentence a screen reader hears is in the page (not on screen).
      await expect(page.getByTestId("code-background")).toHaveText(
        "5 helpers are still working in the background. Their report will appear here when they finish.",
      );

      // Two helpers end before the next read (the view reads again every 4 s).
      stage.now = helpers(3, 4);
      await expect(page.getByTestId("code-sign-primary")).toHaveText("3 helpers working", { timeout: 15_000 });
      await expect(page.getByTestId("code-sign-secondary")).toHaveText("2 finished");
      await expect(page.getByTestId("code-sign-ring")).toHaveAttribute("data-done", "2");

      // At the end of the conversation the sign rests in its own place, UNDER
      // the last line (never on it), with the usual room between two rows.
      const rest = await page.evaluate(() => {
        const q = (id: string) => document.querySelector<HTMLElement>(`[data-testid='${id}']`)!;
        const rows = q("code-conversation");
        const sc = q("code-transcript");
        return {
          atEnd: sc.scrollHeight - sc.scrollTop - sc.clientHeight < 1,
          gap: q("code-working-sign").getBoundingClientRect().top - rows.lastElementChild!.getBoundingClientRect().bottom,
        };
      });
      expect(rest.atEnd).toBe(true);
      expect(rest.gap).toBe(8);

      // On screen, inside the window; nothing pans sideways.
      const at = (await sign.boundingBox())!;
      expect(at.x).toBeGreaterThanOrEqual(0);
      expect(at.x + at.width).toBeLessThanOrEqual(size.width + 0.5);
      expect(at.height).toBe(32);
      const box = (await page.getByTestId("code-composer").boundingBox())!;
      expect(at.y + at.height).toBeLessThanOrEqual(box.y + 0.5);
      expect(await widest(page)).toBeLessThanOrEqual(1);

      // The light rests on one working segment at a time: 3 working of 5, so
      // three places a fifth of a turn apart, and nothing in between.
      const seen = new Set<string>();
      for (let i = 0; i < 16; i++) {
        seen.add(await page.getByTestId("code-sign-light").evaluate((el) => getComputedStyle(el).transform));
        await page.waitForTimeout(200);
      }
      expect(seen.size).toBeGreaterThanOrEqual(2);
      expect(seen.size).toBeLessThanOrEqual(3);
      const turns = Array.from(seen, (m) => {
        if (m === "none") return 0;
        const [a, b] = m.replace("matrix(", "").split(",").map(Number);
        return Math.round(((Math.atan2(b, a) * 180) / Math.PI + 360) % 360);
      });
      for (const deg of turns) expect([0, 72, 144]).toContain(deg);
      await shot(page, `helpers-${size.width}-${theme}`);

      // Everything ends: the sign goes and NOTHING MOVES.
      const before = await places(page);
      stage.now = quiet(4);
      await expect(sign).toHaveCount(0, { timeout: 15_000 });
      const after = await places(page);
      expect(after.box).toBe(before.box);
      expect(after.last).toBe(before.last);
      expect(after.scroll).toBe(before.scroll);
      await shot(page, `quiet-${size.width}-${theme}`);
    });
  }
}

test("a reader who scrolled up still sees it, just above the message box, with a pill behind it", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const stage = { now: helpers(4, 60) };
  await stub(page, stage);
  await open(page, "dark");
  const sign = page.getByTestId("code-working-sign");
  await expect(sign).toBeVisible();
  // At the end of the conversation it has no box.
  await expect(sign).toHaveAttribute("data-floating", "false");
  const flat = await sign.evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(flat).toBe("rgba(0, 0, 0, 0)");

  await page.getByTestId("code-transcript").evaluate((el) => {
    el.scrollTop = 0;
  });
  await expect(sign).toHaveAttribute("data-floating", "true");
  const lifted = await sign.evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(lifted).not.toBe("rgba(0, 0, 0, 0)");
  // Still on screen: inside the conversation's box, just above the message box.
  const at = (await sign.boundingBox())!;
  const transcript = (await page.getByTestId("code-transcript").boundingBox())!;
  const box = (await page.getByTestId("code-composer").boundingBox())!;
  expect(at.y).toBeGreaterThanOrEqual(transcript.y);
  // 8 px above the conversation's bottom edge, which is the message box's top.
  expect(transcript.y + transcript.height - (at.y + at.height)).toBe(8);
  expect(box.y - (at.y + at.height)).toBe(8);
  // The message box still takes words with the sign above it.
  await page.getByTestId("code-box").fill("how is it going?");
  await expect(page.getByTestId("code-box")).toHaveValue("how is it going?");
  await shot(page, "scrolled-up-1280-dark");
});

test("a reply being written wears the same ring, and with 'reduce motion' on nothing is animated", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const stage: { now: Stage } = {
    now: {
      session: { running: true },
      events: [
        { type: "user", text: "check every report page and fix what is broken", turn: "t1", at: "2026-11-10T09:00:00Z" },
        { type: "text", item: "m1:0", delta: "Looking at the report pages now.", turn: "t1", at: "2026-11-10T09:00:03Z" },
      ],
      running: "t1",
    },
  };
  await stub(page, stage);
  await open(page, "dark");
  const working = page.getByTestId("code-working");
  await expect(working).toBeVisible();
  await expect(working).toHaveText("Claude Code is working…");
  await expect(page.getByTestId("code-working-sign")).toHaveAttribute("data-sign", "reply");
  await expect(page.getByTestId("code-working-sign")).toHaveAttribute("data-motion", "on");
  expect(await page.getByTestId("code-sign-light").evaluate((el) => getComputedStyle(el).animationName)).toBe("wsign-hop");
  await shot(page, "reply-1280-dark");

  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.getByTestId("code-working-sign")).toHaveAttribute("data-motion", "off");
  expect(await page.getByTestId("code-sign-light").evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
  await expect(working).toHaveText("Claude Code is working…");
  await expect(page.getByTestId("code-sign-ring")).toBeVisible();
  await shot(page, "reply-still-1280-dark");
});

test("it follows a theme the user made (the variables such a theme sets)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const stage = { now: helpers(5) };
  await stub(page, stage);
  await open(page, "dark");
  await expect(page.getByTestId("code-working-sign")).toBeVisible();
  stage.now = helpers(3, 4);
  await expect(page.getByTestId("code-sign-secondary")).toHaveText("2 finished", { timeout: 15_000 });
  // A made theme sets these variables on the page (lib/theme applyTheme).
  await page.evaluate(() => {
    const root = document.documentElement.style;
    root.setProperty("--accent-rgb", "167 139 250");
    root.setProperty("--accent-soft-rgb", "196 181 253");
    root.setProperty("--tone-success", "250 204 21");
  });
  const light = await page.getByTestId("code-sign-light").locator("circle").evaluate((el) => getComputedStyle(el).stroke);
  expect(light).toBe("rgb(196, 181, 253)");
  const done = await page
    .getByTestId("code-sign-ring")
    .locator("svg")
    .first()
    .locator("circle")
    .first()
    .evaluate((el) => getComputedStyle(el).stroke);
  expect(done).toBe("rgba(250, 204, 21, 0.8)");
  await shot(page, "made-theme-1280");
});

/**
 * WHAT THE LIGHT COSTS — a measurement, run by hand (`IJ_SIGN_MEASURE=1`).
 *
 * A coding session can work for hours, so the sign must not add a cost for
 * every frame the screen draws (v1.415.0 measured that this is what idle
 * motion costs in this app). Three versions of the same page, 20 s each,
 * three rounds, one after the other: the sign standing still, the sign as
 * it ships (the light hops in steps), and — for contrast only — the same
 * light turning smoothly. The browser's own count of processor time
 * (`SystemInfo.getProcessInfo`) is read before and after each.
 */
test("what the light costs: standing still, in steps, and (for contrast) turning smoothly", async ({ page, browser }) => {
  test.skip(!process.env.IJ_SIGN_MEASURE, "a measurement, run by hand");
  test.setTimeout(600_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const stage = { now: helpers(5) };
  await stub(page, stage);
  await open(page, "dark");
  await expect(page.getByTestId("code-working-sign")).toBeVisible();
  await page.addStyleTag({ content: "/* sign-measure */" });
  const setCss = (css: string) =>
    page.evaluate((text) => {
      const tags = Array.from(document.querySelectorAll("style"));
      const mine = tags.find((t) => (t.textContent || "").startsWith("/* sign-measure */"))!;
      mine.textContent = `/* sign-measure */ ${text}`;
    }, css);

  const cdp = await browser.newBrowserCDPSession();
  const read = async () => {
    const { processInfo } = (await cdp.send("SystemInfo.getProcessInfo")) as {
      processInfo: { type: string; id: number; cpuTime: number }[];
    };
    const sum = (type: string) => processInfo.filter((p) => p.type === type).reduce((n, p) => n + p.cpuTime, 0);
    return { gpu: sum("GPU"), renderer: sum("renderer") };
  };

  const versions: Record<string, string> = {
    still: ".wsign-hop, .wsign-tick { animation: none !important; }",
    steps: "",
    smooth: ".wsign-hop { animation: wsign-hop 2s linear infinite !important; --wsign-sweep: 360deg; }",
  };
  const SECONDS = 20;
  const rows: { version: string; gpu: number; renderer: number }[] = [];
  for (let round = 0; round < 3; round++) {
    for (const [version, css] of Object.entries(versions)) {
      await setCss(css);
      await page.waitForTimeout(2_000);
      const a = await read();
      await page.waitForTimeout(SECONDS * 1_000);
      const b = await read();
      rows.push({
        version,
        gpu: ((b.gpu - a.gpu) / SECONDS) * 100,
        renderer: ((b.renderer - a.renderer) / SECONDS) * 100,
      });
    }
  }
  for (const version of Object.keys(versions)) {
    const mine = rows.filter((r) => r.version === version);
    const mean = (k: "gpu" | "renderer") => mine.reduce((n, r) => n + r[k], 0) / mine.length;
    console.log(
      `[cost] ${version}: graphics process ${mean("gpu").toFixed(2)}% of one core, page ${mean("renderer").toFixed(2)}%` +
        `  (rounds: ${mine.map((r) => `${r.gpu.toFixed(2)}/${r.renderer.toFixed(2)}`).join(", ")})`,
    );
  }
});
