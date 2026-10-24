import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The composer's MODEL MENU stays inside the window (the user's report of
 * 2026-10-24: "in chat the module, the model selection is cut off" — a new
 * chat at about 1406 × 857, the menu opened upward and its top, with the
 * "Type to find a model" box and Recent, was cut off).
 *
 * In a real browser against the scratch daemon, with a `/models` answer as
 * tall as the user's (15 providers, one of them with 40 models — stubbed in
 * the browser, so the daemon's own catalog does not matter), the menu is
 * opened from:
 *
 *   (a) a new chat (the empty hero: the composer in the middle of the window),
 *   (b) a chat with messages (the composer at the bottom),
 *   (c) the IJ harness composer — an empty session, and one with a reply,
 *
 * at 1406 × 857, 1280 × 720, 1024 × 640 and 390 × 844, and must:
 *
 *   - sit wholly inside the window (top ≥ 0, bottom ≤ the window's height,
 *     left ≥ 0, right ≤ its width) and never make the page pan sideways;
 *   - be ON SCREEN where it is drawn — its top and bottom edges are what the
 *     browser hits at those points (an ancestor's `overflow` clipping it, as
 *     the chat's section did, fails here even when the box says it is
 *     inside the window);
 *   - show the filter box, take the focus in it, and keep its last row
 *     reachable by scrolling the list;
 *   - (the chat) open a long provider's side panel inside the window too.
 *
 * Measurements (and a screenshot per case) go to IJ_MENU_FIT_OUT when set.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const WEB = `http://127.0.0.1:${process.env.IJ_E2E_WEB_PORT || 8808}`;
const OUT = process.env.IJ_MENU_FIT_OUT || "";

const SIZES = [
  { width: 1406, height: 857 },
  { width: 1280, height: 720 },
  { width: 1024, height: 640 },
  { width: 390, height: 844 },
] as const;

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
};
async function stub(route: Route, body: string): Promise<void> {
  if (route.request().method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS });
    return;
  }
  await route.fulfill({ status: 200, headers: { ...CORS, "content-type": "application/json" }, body });
}

/** A catalog as tall as the user's: 15 providers (three offline), local
 *  endpoints, subscriptions and API keys, and one provider with 40 models. */
function tallCatalog() {
  const row = (provider: string, model: string, kind: string, extra: Record<string, unknown> = {}) => ({
    provider,
    model,
    kind,
    available: true,
    inherited_from: null,
    ...extra,
  });
  const local = { on_this_network: true };
  return [
    row("fleet-rtx", "glm-5.3-flash", "local", { ...local, name: "RTX6000ada", size_b: 30 }),
    row("fleet-rtx", "qwen3-coder-30b", "local", { ...local, name: "RTX6000ada", size_b: 30 }),
    row("ollama", "qwen3:8b", "local", { ...local, size_b: 8 }),
    row("ollama", "llama3.2:3b", "local", { ...local, size_b: 3 }),
    row("fleet", "fleet", "local", { ...local, name: "Your model server" }),
    row("opencode-cli", "opencode/big-pickle", "cli"),
    row("opencode-cli", "opencode/grok-code", "cli"),
    row("claude-cli", "subscription", "cli"),
    row("claude-cli", "claude-opus-4-8", "cli", { label: "Opus 4.8" }),
    row("claude-cli", "claude-sonnet-4-6", "cli", { label: "Sonnet 4.6" }),
    row("codex-cli", "subscription", "cli"),
    row("codex-cli", "gpt-5.5", "cli"),
    row("grok-cli", "grok-4.7", "cli", { label: "Grok 4.7" }),
    row("anthropic", "claude-haiku-4-5", "api"),
    row("anthropic", "claude-sonnet-4-6", "api", { label: "Sonnet 4.6" }),
    row("openai", "gpt-5.5", "api"),
    row("openai", "gpt-5.5-mini", "api"),
    ...Array.from({ length: 40 }, (_, i) => row("openrouter", `vendor-${i + 1}/model-${i + 1}`, "api")),
    row("deepseek", "deepseek-chat", "api"),
    row("mistral", "mistral-large", "api"),
    row("custom", "my-model", "local", { available: false, name: "Custom endpoint" }),
    row("google", "gemini-3.5-pro", "api", { available: false }),
    row("xai", "grok-4", "api", { available: false }),
  ];
}

const RECENT = ["fleet-rtx::glm-5.3-flash", "claude-cli::claude-opus-4-8", "openai::gpt-5.5"];

async function seed(page: Page) {
  const models = JSON.stringify({ models: tallCatalog() });
  await page.route(`${DAEMON}/models`, (route) => stub(route, models));
  await page.addInitScript((recent) => {
    try {
      window.localStorage.setItem("ij_chat_model_recent", JSON.stringify(recent));
    } catch {
      /* private window */
    }
  }, RECENT);
}

interface Box {
  top: number;
  bottom: number;
  left: number;
  right: number;
}
interface Measure {
  vw: number;
  vh: number;
  pan: number;
  chip: Box;
  menu: Box & { height: number };
  filter: Box;
  /** The browser hits the menu at its top and bottom edges (not clipped). */
  topHit: boolean;
  bottomHit: boolean;
  /** The filter box is what the browser hits at its centre. */
  filterHit: boolean;
  /** The menu's last row, scrolled into view, is hit at its centre. */
  lastRowHit: boolean;
  /** The list scrolls inside the menu (and the filter is not in it). */
  listScrolls: boolean;
  filterInList: boolean;
}

/** The menu as it opened: its box and what the browser hits at its edges —
 *  read before anything is scrolled. */
async function measure(page: Page): Promise<Omit<Measure, "lastRowHit">> {
  return page.evaluate(() => {
    const menu = document.querySelector('[data-testid="model-menu"]') as HTMLElement;
    const filter = document.querySelector('[data-testid="model-filter"]') as HTMLElement;
    const chip = document.querySelector('[title="Switch model"][aria-expanded="true"]') as HTMLElement;
    const box = (r: DOMRect) => ({ top: r.top, bottom: r.bottom, left: r.left, right: r.right });
    const m = menu.getBoundingClientRect();
    const f = filter.getBoundingClientRect();
    const cx = m.left + m.width / 2;
    const inMenu = (x: number, y: number) => {
      const hit = document.elementFromPoint(x, y);
      return !!hit && menu.contains(hit);
    };
    const filterHitEl = document.elementFromPoint(f.left + f.width / 2, f.top + f.height / 2);
    // The element that scrolls the rows: the menu itself or a list inside it.
    const scrollers = [menu, ...Array.from(menu.querySelectorAll<HTMLElement>("*"))].filter((el) => {
      const s = getComputedStyle(el);
      return /(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 1;
    });
    const list = scrollers[0] ?? null;
    return {
      vw: window.innerWidth,
      vh: window.innerHeight,
      pan: document.documentElement.scrollWidth - window.innerWidth,
      chip: box(chip.getBoundingClientRect()),
      menu: { ...box(m), height: m.height },
      filter: box(f),
      topHit: inMenu(cx, m.top + 2),
      bottomHit: inMenu(cx, m.bottom - 2),
      filterHit: !!filterHitEl && (filterHitEl === filter || filter.contains(filterHitEl)),
      listScrolls: !!list,
      filterInList: !!list && list !== menu && list.contains(filter),
    };
  });
}

/** The menu's last row, brought into view the way a user would (scrolling
 *  the list), is what the browser hits at its centre. */
async function lastRowReachable(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const menu = document.querySelector('[data-testid="model-menu"]') as HTMLElement;
    const rows = Array.from(menu.querySelectorAll<HTMLElement>("button"));
    const last = rows[rows.length - 1];
    if (!last) return false;
    last.scrollIntoView({ block: "nearest" });
    const l = last.getBoundingClientRect();
    const hit = document.elementFromPoint(l.left + l.width / 2, l.top + l.height / 2);
    return !!hit && (hit === last || last.contains(hit));
  });
}

function record(name: string, data: unknown) {
  if (!OUT) return;
  mkdirSync(OUT, { recursive: true });
  writeFileSync(join(OUT, `${name}.json`), `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

async function shot(page: Page, name: string) {
  if (!OUT) return;
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: join(OUT, `${name}.png`) });
}

/** Every rule the menu must keep, soft so one run reports every case. */
function expectFits(m: Measure, label: string) {
  expect.soft(m.menu.top, `${label}: menu top`).toBeGreaterThanOrEqual(0);
  expect.soft(m.menu.bottom, `${label}: menu bottom`).toBeLessThanOrEqual(m.vh);
  expect.soft(m.menu.left, `${label}: menu left`).toBeGreaterThanOrEqual(0);
  expect.soft(m.menu.right, `${label}: menu right`).toBeLessThanOrEqual(m.vw);
  expect.soft(m.pan, `${label}: sideways pan`).toBeLessThanOrEqual(0);
  expect.soft(m.topHit, `${label}: the menu's top edge is on screen (not clipped)`).toBe(true);
  expect.soft(m.bottomHit, `${label}: the menu's bottom edge is on screen (not clipped)`).toBe(true);
  expect.soft(m.filterHit, `${label}: the filter box is on screen`).toBe(true);
  expect.soft(m.lastRowHit, `${label}: the last row is reachable`).toBe(true);
  expect.soft(m.filterInList, `${label}: the filter is not inside the scrolling list`).toBe(false);
}

/** A user's click: once the chip has stopped moving (a chat that just opened
 *  glides its composer to the bottom for 300 ms), the mouse at its centre.
 *  NOT `locator.click()`: Playwright scrolls the element "into view" first,
 *  and caught mid-glide it scrolled the chat's clipped section by up to
 *  402 px — a scroll no user can make or undo (measured; the old menu's
 *  focus scroll happened to put it back). */
async function settledClick(page: Page, el: ReturnType<Page["getByTitle"]>) {
  const settled = async () => {
    let last = "";
    for (let i = 0; i < 40; i++) {
      const b = await el.boundingBox();
      const now = b ? `${Math.round(b.x)},${Math.round(b.y)},${Math.round(b.width)}` : "";
      if (b && now === last) return b;
      last = now;
      await page.waitForTimeout(150);
    }
    throw new Error("the chip never stopped moving");
  };
  let b = await settled();
  const vh = page.viewportSize()?.height ?? 0;
  if (b.y < 0 || b.y + b.height > vh) {
    // Below the fold (the IJ harness at 1024 × 640 and 390 × 844): the user
    // scrolls the page to the composer first — once nothing moves any more.
    await el.scrollIntoViewIfNeeded();
    b = await settled();
  }
  await page.mouse.click(b.x + b.width / 2, b.y + b.height / 2);
}

async function openMenu(page: Page, firstRow: string) {
  const chip = page.getByTitle("Switch model");
  await expect(chip).toBeVisible({ timeout: 90_000 });
  const menu = page.getByTestId("model-menu");
  // A cold dev route can draw the chip before React has hydrated it, and a
  // click then does nothing: press again (only while no menu is open).
  for (let i = 0; i < 5 && (await menu.count()) === 0; i++) {
    await settledClick(page, chip);
    await menu.waitFor({ state: "visible", timeout: 4_000 }).catch(() => {});
  }
  await expect(menu).toBeVisible();
  await expect(menu.getByText(firstRow, { exact: true }).first()).toBeAttached({ timeout: 30_000 });
  return menu;
}

/** The chat: pick the model the user had (glm-5.3-flash) by typing in the
 *  filter box and pressing Enter (keys only: nothing is scrolled or clicked,
 *  so the menu measured next opens on an untouched page), so the menu carries
 *  "Make this my default" as in the report, then open it again. */
async function openChatMenu(page: Page) {
  let menu = await openMenu(page, "Claude Code");
  await expect(page.getByTestId("model-filter")).toBeFocused();
  await page.keyboard.type("glm-5.3");
  await page.keyboard.press("Enter");
  await expect(menu).toHaveCount(0);
  await expect(page.getByTitle("Switch model")).toContainText("glm-5.3-flash");
  menu = await openMenu(page, "Claude Code");
  await expect(menu.getByTestId("model-make-default")).toBeAttached();
  return menu;
}

async function checkFilterFocus(page: Page, label: string) {
  const filter = page.getByTestId("model-filter");
  await filter.focus();
  await expect.soft(filter, `${label}: the filter takes the focus`).toBeFocused();
  await filter.fill("sonnet");
  await expect.soft(page.getByTestId("model-match").first(), `${label}: typing filters`).toBeVisible();
  await filter.fill("");
  // The menu sits at the end of the page now; Shift+Tab from its box still
  // goes back to the chip, as when the menu was drawn right after it.
  await filter.focus();
  await page.keyboard.press("Shift+Tab");
  await expect.soft(page.getByTitle("Switch model"), `${label}: Shift+Tab goes back to the chip`).toBeFocused();
}

async function checkSidePanel(page: Page, label: string) {
  const menu = page.getByTestId("model-menu");
  const row = menu.getByRole("button", { name: /^OpenRouter/ });
  // A row the user cannot reach (cut off by the window or a parent) cannot
  // be clicked: that is a failure to report, not a reason to stop.
  const reached = await row
    .click({ timeout: 5_000 })
    .then(() => true)
    .catch(() => false);
  expect.soft(reached, `${label}: the OpenRouter row can be clicked`).toBe(true);
  if (!reached) return { reached };
  const panel = page.getByTestId("model-sub");
  await expect(panel).toBeVisible();
  const p = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="model-sub"]') as HTMLElement;
    const r = el.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + 4);
    // Review: on a phone the panel opens under its row, inside what the
    // menu's list shows (beside the menu it was shifted over the rows).
    const list = document.querySelector('[data-testid="model-menu-list"]') as HTMLElement;
    const lr = list.getBoundingClientRect();
    return {
      vw: window.innerWidth,
      vh: window.innerHeight,
      top: r.top,
      bottom: r.bottom,
      left: r.left,
      right: r.right,
      topHit: !!hit && el.contains(hit),
      inList:
        list.contains(el) &&
        r.top >= lr.top - 0.5 &&
        r.bottom <= lr.bottom + 0.5 &&
        r.left >= lr.left - 0.5 &&
        r.right <= lr.right + 0.5,
    };
  });
  expect.soft(p.top, `${label}: side panel top`).toBeGreaterThanOrEqual(0);
  expect.soft(p.bottom, `${label}: side panel bottom`).toBeLessThanOrEqual(p.vh);
  expect.soft(p.left, `${label}: side panel left`).toBeGreaterThanOrEqual(0);
  expect.soft(p.right, `${label}: side panel right`).toBeLessThanOrEqual(p.vw);
  expect.soft(p.topHit, `${label}: side panel's top on screen`).toBe(true);
  if (p.vw < 640) expect.soft(p.inList, `${label}: on a phone the panel is under its row, inside the list`).toBe(true);
  // Left open: typing in the filter next replaces the provider rows (and
  // with them the panel). On a phone the panel is shifted over the menu.
  return { reached, ...p };
}

// NOT serial: every case is measured even when one fails (the soft checks
// report every rule of a case; a worker restarted after a failure runs the
// setup below again).

let work = "";
let threadId = "";
let emptySession = "";
let busySession = "";

test.beforeAll(async ({ request, browser }) => {
  work = mkdtempSync(join(tmpdir(), "ij-menu-fit-"));
  // (b) a chat with messages: the composer sits at the bottom.
  const messages = Array.from({ length: 12 }, (_, i) => ({
    role: i % 2 ? "assistant" : "user",
    content: i % 2 ? `Here is answer ${i}. `.repeat(12) : `Question ${i}?`,
  }));
  const t = await request.put(`${DAEMON}/chat/threads/new`, { data: { title: "Menu fit", messages } });
  expect(t.ok()).toBe(true);
  threadId = ((await t.json()) as { id: string }).id;
  // (c) IJ harness sessions in a scratch folder: one left empty, one given a
  // reply below.
  const a = await request.post(`${DAEMON}/build/sessions`, { data: { folder: work, branch: false } });
  expect(a.ok()).toBe(true);
  emptySession = ((await a.json()) as { id: string }).id;
  const b = await request.post(`${DAEMON}/build/sessions`, { data: { folder: work, branch: false } });
  expect(b.ok()).toBe(true);
  busySession = ((await b.json()) as { id: string }).id;
  // Its first turn on the demo model (the scripted step writes RESULT.md in
  // the scratch folder).
  const page = await browser.newPage();
  try {
    await page.goto(`${WEB}/terminals?session=${busySession}`);
    await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
    const box = page.getByTestId("harness-input");
    await box.fill("Write a hello file");
    await box.press("Enter");
    await expect(page.getByTestId("harness-reply").last()).toBeVisible({ timeout: 90_000 });
  } finally {
    await page.close();
  }
});

test.afterAll(() => {
  try {
    if (work) rmSync(work, { recursive: true, force: true });
  } catch {
    /* a shell the daemon still holds can lock it for a moment: the OS clears temp */
  }
});

for (const size of SIZES) {
  const tag = `${size.width}x${size.height}`;
  test.describe(`at ${tag}`, () => {
    test.use({ viewport: { width: size.width, height: size.height } });

    /** Open, measure, picture, then the rows and (the chat) the side panel. */
    async function check(page: Page, name: string, label: string, chat: boolean) {
      const opened = await measure(page);
      await shot(page, name);
      const m: Measure = { ...opened, lastRowHit: await lastRowReachable(page) };
      const side = chat ? await checkSidePanel(page, label) : undefined;
      record(name, side ? { ...m, side } : m);
      expectFits(m, label);
      await checkFilterFocus(page, label);
    }

    test(`(a) a new chat: the menu fits`, async ({ page }) => {
      await seed(page);
      await page.goto("/?new=1");
      await expect(page.getByTestId("chat-greeting")).toBeVisible({ timeout: 90_000 });
      await openChatMenu(page);
      await check(page, `chat-hero-${tag}`, `hero ${tag}`, true);
    });

    test(`(b) a chat with messages: the menu fits`, async ({ page }) => {
      await seed(page);
      await page.goto(`/chat?thread=${threadId}`);
      await expect(page.getByText("Question 10?")).toBeVisible({ timeout: 90_000 });
      await openChatMenu(page);
      await check(page, `chat-thread-${tag}`, `thread ${tag}`, true);
    });

    for (const which of ["empty", "reply"] as const) {
      test(`(c) the IJ harness, ${which === "empty" ? "an empty session" : "a session with a reply"}: the menu fits`, async ({
        page,
      }) => {
        await seed(page);
        const id = which === "empty" ? emptySession : busySession;
        await page.goto(`/terminals?session=${id}`);
        await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
        if (which === "reply") await expect(page.getByTestId("harness-reply").first()).toBeVisible({ timeout: 30_000 });
        await openMenu(page, "Claude Code");
        await check(page, `harness-${which}-${tag}`, `harness ${which} ${tag}`, false);
      });
    }
  });
}

/* ------------------------------------------------------------- the review */

/** The menu against its chip, and an open provider panel against its row:
 *  `gap` is the space between the chip and the menu on the menu's side (6 px
 *  when attached); `panelGap` the space between the panel and its row (4 px);
 *  `panelEdge` how far the panel's anchored edge is from its row's (0). */
async function attached(page: Page) {
  return page.evaluate(() => {
    const menu = document.querySelector('[data-testid="model-menu"]') as HTMLElement | null;
    const chip = document.querySelector('[title="Switch model"][aria-expanded="true"]') as HTMLElement | null;
    if (!menu || !chip) return null;
    const m = menu.getBoundingClientRect();
    const c = chip.getBoundingClientRect();
    const sub = document.querySelector('[data-testid="model-sub"]') as HTMLElement | null;
    const row = sub?.parentElement?.getBoundingClientRect();
    const s = sub?.getBoundingClientRect();
    return {
      gap: menu.dataset.side === "below" ? m.top - c.bottom : c.top - m.bottom,
      chipTop: c.top,
      panelGap: s && row ? row.left - s.right : null,
      panelEdge: s && row ? (sub?.dataset.side === "down" ? s.top - row.top : row.bottom - s.bottom) : null,
    };
  });
}

test.describe("review: the menu stays with its chip (1406 × 857)", () => {
  test.use({ viewport: { width: 1406, height: 857 } });

  /** A new chat: the composer sits in the middle, under the demo strip the
   *  scratch daemon's demo model puts up. */
  async function heroMenu(page: Page) {
    await seed(page);
    await page.goto("/?new=1");
    await expect(page.getByTestId("chat-greeting")).toBeVisible({ timeout: 90_000 });
    await expect(page.getByTestId("simulated-banner")).toBeVisible({ timeout: 30_000 });
    return openMenu(page, "Claude Code");
  }

  /** What "Make this my default" does to the page: the demo strip goes, and
   *  the new chat's centred composer moves up — the window keeps its size. */
  async function stripGoes(page: Page) {
    const before = (await attached(page))?.chipTop ?? 0;
    await page.getByTestId("simulated-banner").evaluate((el) => {
      (el as HTMLElement).style.display = "none";
    });
    await expect
      .poll(async () => Math.abs(((await attached(page))?.chipTop ?? before) - before), {
        message: "the chip moved when the strip went",
      })
      .toBeGreaterThan(10);
  }

  test("the demo strip goes away: the menu follows its chip", async ({ page }) => {
    await heroMenu(page);
    expect((await attached(page))?.gap).toBeCloseTo(6, 0);
    await stripGoes(page);
    await expect
      .poll(async () => (await attached(page))?.gap ?? -1, { message: "the menu's gap to its chip" })
      .toBeCloseTo(6, 0);
  });

  test("a provider's open panel moves with the menu", async ({ page }) => {
    await heroMenu(page);
    await page.getByTestId("model-menu").getByRole("button", { name: /^OpenRouter/ }).click();
    await expect(page.getByTestId("model-sub")).toBeVisible();
    const before = await attached(page);
    expect(before?.panelGap).toBeCloseTo(4, 0);
    expect(before?.panelEdge).toBeCloseTo(0, 0);
    // Something above the new chat's composer grows (a notice, a longer
    // greeting): the centred composer moves and nothing scrolls. (The strip
    // going away comes with scroll events, which re-placed the panel on
    // their own — measured: that case could not tell whether the panel
    // follows its menu.)
    const chipBefore = before?.chipTop ?? 0;
    await page.getByTestId("chat-greeting").evaluate((el) => {
      (el as HTMLElement).style.paddingTop = "80px";
    });
    await expect
      .poll(async () => Math.abs(((await attached(page))?.chipTop ?? chipBefore) - chipBefore), {
        message: "the chip moved",
      })
      .toBeGreaterThan(10);
    await expect
      .poll(async () => (await attached(page))?.panelEdge ?? -99, { message: "the panel's edge against its row" })
      .toBeCloseTo(0, 0);
    expect((await attached(page))?.panelGap).toBeCloseTo(4, 0);
  });

  test("an ancestor that makes `fixed` relative to itself (a transform, a backdrop filter) cannot move the menu", async ({
    page,
  }) => {
    await seed(page);
    await page.goto(`/chat?thread=${threadId}`);
    await expect(page.getByText("Question 10?")).toBeVisible({ timeout: 90_000 });
    // What Mark 8's glass or an animating wrapper does to a `position: fixed`
    // box inside it: the box is placed in that ancestor, not in the window.
    // The menu lives on the page's top layer (a portal), out of its reach.
    await page.getByTestId("chat-composer").evaluate((el) => {
      const host = el.parentElement as HTMLElement;
      host.style.transform = "translateZ(0)";
      host.style.backdropFilter = "blur(1px)";
    });
    await openMenu(page, "Claude Code");
    const m = await measure(page);
    expect.soft(m.menu.top, "menu top").toBeGreaterThanOrEqual(0);
    expect.soft(m.menu.bottom, "menu bottom").toBeLessThanOrEqual(m.vh);
    expect.soft(m.topHit && m.bottomHit && m.filterHit, "the menu is on screen where it is drawn").toBe(true);
    expect((await attached(page))?.gap, "the menu at its chip").toBeCloseTo(6, 0);
  });

  test("the app's palette opened over an open menu covers it, never the other way round", async ({ page }) => {
    await seed(page);
    await page.goto(`/chat?thread=${threadId}`);
    await expect(page.getByText("Question 10?")).toBeVisible({ timeout: 90_000 });
    await openMenu(page, "Claude Code");
    // Opened the way the title bar's search opens it (open, never toggle):
    // the palette is a chunk loaded when the page is idle, and a Ctrl+K
    // pressed before it arrived would open nothing.
    const dialog = page.getByRole("dialog", { name: "Search Iron Jarvis" });
    await expect
      .poll(async () => {
        if ((await dialog.count()) === 0) await page.evaluate(() => window.dispatchEvent(new CustomEvent("ij:open-palette")));
        return dialog.count();
      }, { message: "the palette opened" })
      .toBe(1);
    await expect(page.getByTestId("model-menu")).toBeVisible();
    const top = await page.evaluate(() => {
      const menu = document.querySelector('[data-testid="model-menu"]') as HTMLElement | null;
      if (!menu) return "closed";
      const r = menu.getBoundingClientRect();
      const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return el && menu.contains(el) ? "menu" : "palette";
    });
    expect(top).not.toBe("menu");
  });
});

test.describe("review: the IJ harness's Ctrl+K from a row of the menu", () => {
  test.use({ viewport: { width: 1406, height: 857 } });
  test("stays in the menu; the app's palette does not open", async ({ page }) => {
    await seed(page);
    await page.goto(`/terminals?session=${emptySession}`);
    await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
    // The palette is a chunk loaded when the page is idle: prove it is here
    // (open it, close it), or a Ctrl+K it would have taken proves nothing.
    const dialog = page.getByRole("dialog", { name: "Search Iron Jarvis" });
    await expect
      .poll(async () => {
        if ((await dialog.count()) === 0) await page.evaluate(() => window.dispatchEvent(new CustomEvent("ij:open-palette")));
        return dialog.count();
      }, { message: "the palette is loaded" })
      .toBe(1);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    const menu = await openMenu(page, "Claude Code");
    await menu.getByText("default model", { exact: true }).focus();
    await page.keyboard.press("Control+k");
    // An absence: give a palette that WOULD open (it opens on the key, within
    // a frame — measured before the fix) the time to show, then look.
    await page.waitForTimeout(600);
    await expect(page.getByTestId("model-menu")).toBeVisible();
    await expect(dialog).toHaveCount(0);
  });
});
