import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The composer's toolbar is ONE ROW whatever model is picked (v1.400.0, the
 * user's report of 2026-10-27: "it looks clean until the model with the
 * longer name is selected and then things start to look a bit off with
 * spacing"). A new chat at about 2000 × 1103 with "Qwen3.8-27B (RTX)" picked
 * — a model that offers a reasoning level, so a "Reasoning" chip appears —
 * dropped the whole right group (Reasoning, the model, the mic, Send) to a
 * second line under the chips.
 *
 * In a real browser against the scratch daemon, with GET /models stubbed to
 * a short-named model with no reasoning, the user's model, two longer ones
 * and one of 50 characters (all four with reasoning levels), the toolbar of
 *
 *   (a) a new chat (the centred composer),
 *   (b) a saved chat (the composer under the conversation),
 *   (c) the IJ harness composer (one Build session per model),
 *
 * is measured at the user's 2000 × 1103 and 1406 × 857, 1280 × 720,
 * 1024 × 640 and 390 × 844. At every size: the page never pans sideways,
 * every control sits inside the composer card and no two controls overlap
 * (a narrow IJ harness column too). Where the composer is at least 640 px
 * wide, also: every visible control shares one vertical centre (± 2 px), Send
 * is the right-most, and the model chip shows at least six characters of its
 * name.
 *
 * Then the user's own case (a light theme, 2000 × 1103 and 1024 × 640: one
 * row); typing and opening each toolbar menu never move a control; the
 * ORDER — narrowing the box, the model's name truncates while the chips on
 * the left keep their words; a few pixels of jitter around the boundary
 * never brings the chips' words back (the hysteresis), and real room does.
 *
 * Every check is soft, so one run reports every case. IJ_ROW_OUT=<folder>
 * writes each case's measurements (JSON) and a screenshot; the failures are
 * also summed in <folder>/summary.json. IJ_ROW_SHOTS=<folder> (with
 * IJ_ROW_SHOTS_TAG) saves the user's case, page and composer.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const OUT = process.env.IJ_ROW_OUT || "";

const SIZES = [
  { width: 2000, height: 1103 },
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

const LEVELS = ["low", "medium", "high"];

/** The models the toolbar is measured with: what to type in the menu's
 *  filter to pick one, and the words the chip then shows. */
const MODELS = [
  { key: "short", provider: "fleet-rtx", model: "brain", type: "brain (rtx)", label: "brain (RTX)", reasoning: "" },
  { key: "qwen", provider: "fleet-rtx", model: "qwen3.8-27b", type: "qwen3.8", label: "Qwen3.8-27B (RTX)", reasoning: "-" },
  {
    key: "glm",
    provider: "fleet-dgx",
    model: "glm-5.3-flash",
    type: "glm-5.3",
    label: "glm-5.3-flash (DGX Spark)",
    reasoning: "medium",
  },
  {
    key: "opus",
    provider: "claude-cli",
    model: "claude-opus-4-8",
    type: "work max",
    label: "claude-opus-4-8 · Work Max",
    reasoning: "high",
  },
  {
    key: "long",
    provider: "fleet-dgx",
    model: "llama-4-maverick-17b-128e-instruct-fp8",
    type: "maverick",
    label: "Llama-4-Maverick-17B-128E-Instruct-FP8 (DGX Spark)",
    reasoning: "auto",
  },
] as const;
type ModelCase = (typeof MODELS)[number];

function catalog() {
  return MODELS.map((m) => ({
    provider: m.provider,
    model: m.model,
    label: m.label,
    kind: m.provider === "claude-cli" ? "cli" : "local",
    available: true,
    inherited_from: null,
    ...(m.provider.startsWith("fleet-")
      ? { on_this_network: true, name: m.provider === "fleet-rtx" ? "RTX-BOX" : "DGX Spark" }
      : {}),
    // "-" = levels with no known default (the chip reads "Reasoning", as in
    // the user's screenshot); "" = no levels at all (no chip).
    ...(m.reasoning ? { reasoning: LEVELS, reasoning_default: m.reasoning === "-" ? "" : m.reasoning } : {}),
  }));
}

async function seed(page: Page) {
  const models = JSON.stringify({ models: catalog() });
  await page.route(`${DAEMON}/models`, (route) => stub(route, models));
}

interface Ctl {
  name: string;
  left: number;
  right: number;
  top: number;
  bottom: number;
}
interface Facts {
  vw: number;
  pan: number;
  card: { left: number; right: number; top: number; bottom: number; width: number };
  controls: Ctl[];
  send: string;
  /** The model chip's visible label, and how wide six of its characters are. */
  labelW: number;
  sixW: number;
  labelText: string;
}

/** The toolbar's visible controls, the card around them, the page's pan. */
async function facts(page: Page, place: "chat" | "harness"): Promise<Facts> {
  return page.evaluate((where) => {
    const card = document.querySelector(
      where === "chat" ? '[data-testid="chat-composer"]' : '[data-testid="harness-composer"]',
    ) as HTMLElement;
    const bar = (
      where === "chat"
        ? card.querySelector('[data-testid="composer-toolbar"]')
        : card.querySelector('[data-testid="harness-box-actions"]')?.parentElement
    ) as HTMLElement;
    const send = (
      where === "chat" ? bar.querySelector('button[aria-label="Send"]') : bar.querySelector('[data-testid="harness-send"]')
    ) as HTMLElement;
    const seen = new Set<Element>();
    const controls: { name: string; left: number; right: number; top: number; bottom: number }[] = [];
    for (const el of Array.from(bar.querySelectorAll<HTMLElement>("button, select"))) {
      // The reasoning level is a native select lying invisibly over its chip:
      // the chip is what the user sees.
      const shown = el.tagName === "SELECT" ? (el.parentElement as HTMLElement) : el;
      if (seen.has(shown)) continue;
      // A menu drawn inside the toolbar (the "+" menu, the tools menu) hangs
      // above it, absolutely placed: its rows are not the toolbar's row.
      let inMenu = false;
      for (let a = shown.parentElement; a && a !== bar; a = a.parentElement) {
        if (getComputedStyle(a).position === "absolute") inMenu = true;
      }
      if (inMenu) continue;
      seen.add(shown);
      const r = shown.getBoundingClientRect();
      const s = getComputedStyle(shown);
      if (!(r.width > 0 && r.height > 0) || s.visibility === "hidden") continue;
      const name =
        shown === send
          ? "Send"
          : el.getAttribute("aria-label") ||
            el.getAttribute("data-testid") ||
            el.getAttribute("title") ||
            (el.textContent || "").trim().slice(0, 24);
      controls.push({ name, left: r.left, right: r.right, top: r.top, bottom: r.bottom });
    }
    const c = card.getBoundingClientRect();
    const label = bar.querySelector('[data-testid="model-chip-words"]') as HTMLElement | null;
    let sixW = 0;
    let labelText = "";
    if (label) {
      labelText = (label.textContent || "").trim();
      const ctx = document.createElement("canvas").getContext("2d");
      if (ctx) {
        const s = getComputedStyle(label);
        ctx.font = `${s.fontWeight} ${s.fontSize} ${s.fontFamily}`;
        sixW = ctx.measureText(labelText.slice(0, 6)).width;
      }
    }
    return {
      vw: window.innerWidth,
      pan: document.documentElement.scrollWidth - window.innerWidth,
      card: { left: c.left, right: c.right, top: c.top, bottom: c.bottom, width: c.width },
      controls,
      send: send ? "Send" : "",
      labelW: label ? label.getBoundingClientRect().width : 0,
      sixW,
      labelText,
    };
  }, place);
}

/** The rules, each soft; returns the names of the rules broken. */
function check(f: Facts, label: string): string[] {
  const broken: string[] = [];
  const rule = (ok: boolean, what: string) => {
    expect.soft(ok, `${label}: ${what}`).toBe(true);
    if (!ok) broken.push(what);
  };
  rule(f.pan <= 0, `no sideways pan (pan ${f.pan})`);
  const outside = f.controls.filter(
    (c) => c.left < f.card.left - 0.5 || c.right > f.card.right + 0.5 || c.top < f.card.top - 0.5 || c.bottom > f.card.bottom + 0.5,
  );
  rule(outside.length === 0, `every control inside the card (outside: ${outside.map((c) => c.name).join(", ")})`);
  // At every width (a narrow IJ harness column too): nothing drawn over
  // anything else.
  const overlaps: string[] = [];
  for (let i = 0; i < f.controls.length; i++) {
    for (let j = i + 1; j < f.controls.length; j++) {
      const a = f.controls[i];
      const b = f.controls[j];
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w > 0.5 && h > 0.5) overlaps.push(`${a.name} × ${b.name}`);
    }
  }
  rule(overlaps.length === 0, `no two controls overlap (${overlaps.join("; ")})`);
  if (f.card.width < 640) return broken;
  const centres = f.controls.map((c) => (c.top + c.bottom) / 2);
  const spread = centres.length ? Math.max(...centres) - Math.min(...centres) : 0;
  rule(spread <= 2, `one row: every control on one vertical centre (spread ${spread.toFixed(1)} px)`);
  const send = f.controls.find((c) => c.name === "Send");
  const rightMost = Math.max(...f.controls.map((c) => c.right));
  rule(!!send && send.right >= rightMost - 0.5, "Send is the right-most control");
  rule(f.labelW + 0.5 >= f.sixW, `the model chip shows at least six characters (${f.labelW.toFixed(0)} px of ${f.sixW.toFixed(0)})`);
  return broken;
}

const failures: Record<string, string[]> = {};
function record(name: string, data: unknown, broken: string[]) {
  failures[name] = broken;
  if (!OUT) return;
  mkdirSync(OUT, { recursive: true });
  writeFileSync(join(OUT, `${name}.json`), `${JSON.stringify({ broken, data }, null, 2)}\n`, "utf8");
}
async function shot(page: Page, name: string) {
  if (!OUT) return;
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: join(OUT, `${name}.png`) });
}

/** Two frames: whatever the toolbar does after a change has been painted. */
async function frames(page: Page) {
  await page.evaluate(
    () => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))),
  );
}

/** A user's click on the model chip once it has stopped moving (a chat that
 *  just opened glides its composer for 300 ms; a click mid-glide misses). */
async function clickChip(page: Page) {
  const chip = page.getByTitle("Switch model");
  await expect(chip).toBeVisible({ timeout: 90_000 });
  let last = "";
  for (let i = 0; i < 40; i++) {
    const b = await chip.boundingBox();
    const now = b ? `${Math.round(b.x)},${Math.round(b.y)}` : "";
    if (b && now === last) {
      const vh = page.viewportSize()?.height ?? 0;
      if (b.y < 0 || b.y + b.height > vh) {
        await chip.scrollIntoViewIfNeeded();
        last = "";
        continue;
      }
      await page.mouse.click(b.x + b.width / 2, b.y + b.height / 2);
      return;
    }
    last = now;
    await page.waitForTimeout(150);
  }
  throw new Error("the model chip never stopped moving");
}

/** Pick a model the way a user can with keys: open the menu, type, Enter. */
async function pick(page: Page, m: ModelCase) {
  const menu = page.getByTestId("model-menu");
  for (let i = 0; i < 5 && (await menu.count()) === 0; i++) {
    await clickChip(page);
    await menu.waitFor({ state: "visible", timeout: 4_000 }).catch(() => {});
  }
  await expect(menu).toBeVisible();
  await expect(page.getByTestId("model-filter")).toBeFocused();
  await page.keyboard.type(m.type);
  await expect(page.getByTestId("model-match").first()).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(menu).toHaveCount(0);
  await settled(page, m);
}

/** The chip names the model, and the reasoning chip is there exactly when
 *  the model offers levels. */
async function settled(page: Page, m: ModelCase) {
  await expect(page.getByTestId("model-chip-words")).toContainText(m.label, { timeout: 30_000 });
  await expect(page.getByTestId("reasoning-level")).toHaveCount(m.reasoning ? 1 : 0, { timeout: 30_000 });
  await frames(page);
}

let work = "";
let threadId = "";
const sessions: Record<string, string> = {};

test.beforeAll(async ({ request }) => {
  work = mkdtempSync(join(tmpdir(), "ij-one-row-"));
  // (b) a saved chat: one question and its answer.
  const t = await request.put(`${DAEMON}/chat/threads/new`, {
    data: {
      title: "One row",
      messages: [
        { role: "user", content: "What is a toolbar?" },
        { role: "assistant", content: "A row of controls." },
      ],
    },
  });
  expect(t.ok()).toBe(true);
  threadId = ((await t.json()) as { id: string }).id;
  // (c) one IJ harness session per model, made on that model.
  for (const m of MODELS) {
    const s = await request.post(`${DAEMON}/build/sessions`, {
      data: { folder: work, branch: false, provider: m.provider, model: m.model },
    });
    expect(s.ok()).toBe(true);
    sessions[m.key] = ((await s.json()) as { id: string }).id;
  }
});

test.afterAll(() => {
  if (OUT) {
    mkdirSync(OUT, { recursive: true });
    let prev: Record<string, string[]> = {};
    try {
      prev = JSON.parse(readFileSync(join(OUT, "summary.json"), "utf8")) as Record<string, string[]>;
    } catch {
      /* first file */
    }
    writeFileSync(join(OUT, "summary.json"), `${JSON.stringify({ ...prev, ...failures }, null, 2)}\n`, "utf8");
  }
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

    test(`(a) a new chat: the toolbar is one row for every model`, async ({ page }) => {
      await seed(page);
      await page.goto("/?new=1");
      await expect(page.getByTestId("chat-greeting")).toBeVisible({ timeout: 90_000 });
      for (const m of MODELS) {
        await pick(page, m);
        const name = `hero-${m.key}-${tag}`;
        const f = await facts(page, "chat");
        await shot(page, name);
        record(name, f, check(f, `hero ${m.key} ${tag}`));
      }
    });

    test(`(b) a saved chat: the toolbar is one row for every model`, async ({ page }) => {
      await seed(page);
      await page.goto(`/chat?thread=${threadId}`);
      await expect(page.getByText("A row of controls.")).toBeVisible({ timeout: 90_000 });
      for (const m of MODELS) {
        await pick(page, m);
        const name = `thread-${m.key}-${tag}`;
        const f = await facts(page, "chat");
        await shot(page, name);
        record(name, f, check(f, `thread ${m.key} ${tag}`));
      }
    });

    test(`(c) the IJ harness: the toolbar is one row for every model`, async ({ page }) => {
      await seed(page);
      for (const m of MODELS) {
        await page.goto(`/terminals?session=${sessions[m.key]}`);
        await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
        await settled(page, m);
        const name = `harness-${m.key}-${tag}`;
        const f = await facts(page, "harness");
        await shot(page, name);
        record(name, f, check(f, `harness ${m.key} ${tag}`));
      }
    });
  });
}

/* ------------------------------------------------- the user's own case */

const QWEN = MODELS[1];
const SHOTS = process.env.IJ_ROW_SHOTS || "";
const SHOTS_TAG = process.env.IJ_ROW_SHOTS_TAG || "shot";

/** Every visible control's box, rounded: what "the row moved" compares. */
async function boxes(page: Page): Promise<string> {
  const f = await facts(page, "chat");
  return JSON.stringify(f.controls.map((c) => [c.name, Math.round(c.left), Math.round(c.top), Math.round(c.right)]));
}
function oneRow(f: Facts): boolean {
  const centres = f.controls.map((c) => (c.top + c.bottom) / 2);
  return Math.max(...centres) - Math.min(...centres) <= 2;
}

for (const size of [
  { width: 2000, height: 1103 },
  { width: 1024, height: 640 },
] as const) {
  const tag = `${size.width}x${size.height}`;
  test.describe(`the user's case at ${tag}, a light theme`, () => {
    test.use({ viewport: { width: size.width, height: size.height } });
    test("Qwen3.8-27B (RTX) with its Reasoning chip: one row", async ({ page }) => {
      await seed(page);
      // The user's screenshots are on a light theme (Mark 8, Liquid Glass).
      await page.addInitScript(() => {
        try {
          window.localStorage.setItem("ij_theme", "mark8");
        } catch {
          /* private window */
        }
      });
      await page.goto("/?new=1");
      await expect(page.getByTestId("chat-greeting")).toBeVisible({ timeout: 90_000 });
      await pick(page, QWEN);
      // The mouse leaves the chip it picked through (its hover fill is not
      // the point), and its 150 ms fade ends.
      await page.mouse.move(2, size.height - 2);
      await page.waitForTimeout(400);
      await frames(page);
      if (SHOTS) {
        mkdirSync(SHOTS, { recursive: true });
        await page.screenshot({ path: join(SHOTS, `${SHOTS_TAG}-hero-qwen-${tag}.png`) });
        await page.getByTestId("chat-composer").screenshot({ path: join(SHOTS, `${SHOTS_TAG}-composer-qwen-${tag}.png`) });
      }
      const f = await facts(page, "chat");
      expect(check(f, `the user's case ${tag}`)).toEqual([]);
    });
  });
}

test.describe("the row holds still (1406 × 857, the user's model)", () => {
  test.use({ viewport: { width: 1406, height: 857 } });

  test("typing, and opening each menu, never move the row", async ({ page }) => {
    await seed(page);
    await page.goto("/?new=1");
    await expect(page.getByTestId("chat-greeting")).toBeVisible({ timeout: 90_000 });
    // A box narrow enough that the chips have given up their words.
    await page.getByTestId("chat-composer").evaluate((el) => {
      (el as HTMLElement).style.width = "600px";
    });
    await pick(page, QWEN);
    await page.mouse.move(2, 2);
    await frames(page);
    const f0 = await facts(page, "chat");
    expect(oneRow(f0), "one row before anything is done").toBe(true);
    // The chips that gave up their words keep their names, and their
    // tooltips lead with the words they no longer show.
    await expect(page.getByTestId("local-only-chip")).toHaveAttribute("aria-label", "Local only");
    await expect(page.getByTestId("local-only-chip")).toHaveAttribute("title", /^Local only: /);
    await expect(page.getByTestId("composer-web")).toHaveAttribute("title", /^Web: /);
    await expect(page.getByTestId("composer-tools")).toHaveAttribute("title", /^Auto tools: /);
    await expect(page.locator("#chat-approval-mode")).toHaveAttribute("aria-label", /^Permissions: /);
    const level = ((await page.locator("#chat-approval-mode").getAttribute("aria-label")) ?? "").replace(/^Permissions: /, "");
    await expect(page.locator("#chat-approval-mode")).toHaveAttribute("title", new RegExp(`^${level}: `));
    const before = await boxes(page);
    // Typing: every keystroke, and the first one that wakes Send.
    await page.getByRole("textbox").first().click();
    await page.keyboard.type("a message long enough to wake Send and keep typing past it", { delay: 5 });
    await frames(page);
    expect(await boxes(page), "typing moved the row").toBe(before);
    await page.keyboard.press("Control+a");
    await page.keyboard.press("Delete");
    await frames(page);
    expect(await boxes(page), "clearing the box moved the row").toBe(before);
    // Each menu the toolbar opens.
    for (const open of [
      () => page.locator("#chat-approval-mode").click(),
      () => page.getByTestId("composer-tools").click(),
      () => page.getByLabel("Open the chat menu").click(),
      () => clickChip(page),
    ]) {
      await open();
      await frames(page);
      expect(await boxes(page), "opening a menu moved the row").toBe(before);
      await page.keyboard.press("Escape");
      await page.mouse.click(2, 300);
      await frames(page);
    }
  });

  test("the model's name gives way before the chips' words", async ({ page }) => {
    await seed(page);
    await page.goto("/?new=1");
    await expect(page.getByTestId("chat-greeting")).toBeVisible({ timeout: 90_000 });
    await pick(page, QWEN);
    await page.mouse.move(2, 2);
    const setWidth = async (w: number) => {
      await page.getByTestId("chat-composer").evaluate((el, px) => {
        (el as HTMLElement).style.width = `${px}px`;
      }, w);
      await frames(page);
    };
    const state = () =>
      page.getByTestId("chat-composer").evaluate((card) => {
        const chip = card.querySelector("#chat-approval-mode") as HTMLElement;
        const words = Array.from(chip.querySelectorAll("span")).find((s) => (s.textContent || "").trim().length > 2);
        const label = card.querySelector('[data-testid="model-chip-words"]') as HTMLElement;
        return {
          words: !!words && words.getBoundingClientRect().width > 0,
          cut: label.scrollWidth - label.getBoundingClientRect().width > 1,
        };
      });
    // Narrow the box until the model's name starts to truncate: the chips
    // on the left must still have their words at every width on the way.
    let cutAt = 0;
    for (let w = 900; w >= 560; w -= 2) {
      await setWidth(w);
      const s = await state();
      expect(s.words, `the chips gave up their words at ${w} px before the model's name gave way`).toBe(true);
      if (s.cut) {
        cutAt = w;
        break;
      }
    }
    expect(cutAt, "the model's name never truncated").toBeGreaterThan(0);
    // Twelve pixels less: the name is shorter still, the chips keep their words.
    await setWidth(cutAt - 12);
    const s = await state();
    expect(s.cut, "the name truncates further").toBe(true);
    expect(s.words, "the chips keep their words while the name can still give way").toBe(true);
    expect(oneRow(await facts(page, "chat")), "one row").toBe(true);
  });

  test("a few pixels of jitter around the boundary never flickers the chips' words", async ({ page }) => {
    await seed(page);
    await page.goto("/?new=1");
    await expect(page.getByTestId("chat-greeting")).toBeVisible({ timeout: 90_000 });
    await pick(page, QWEN);
    await page.mouse.move(2, 2);
    const setWidth = async (w: number) => {
      await page.getByTestId("chat-composer").evaluate((el, px) => {
        (el as HTMLElement).style.width = `${px}px`;
      }, w);
      await frames(page);
    };
    /** The permission chip's words are on screen. */
    const words = () =>
      page.locator("#chat-approval-mode").evaluate((el) => {
        const label = Array.from(el.querySelectorAll("span")).find((s) => (s.textContent || "").trim().length > 2);
        return !!label && label.getBoundingClientRect().width > 0;
      });
    // Narrow the box two pixels at a time until the words give way: the row
    // must stay one row at every width on the way.
    let boundary = 0;
    for (let w = 792; w >= 560; w -= 2) {
      await setWidth(w);
      const f = await facts(page, "chat");
      expect.soft(oneRow(f), `one row at ${w} px`).toBe(true);
      if (!(await words())) {
        boundary = w;
        break;
      }
    }
    expect(boundary, "the chips never gave up their words: the row wrapped instead").toBeGreaterThan(0);
    // Back and forth by a few pixels: the words stay away, the row stays one.
    for (const w of [boundary + 2, boundary - 1, boundary + 4, boundary, boundary + 6, boundary + 1, boundary + 8]) {
      await setWidth(w);
      expect.soft(await words(), `the words came back at ${w} px (gave way at ${boundary})`).toBe(false);
      expect.soft(oneRow(await facts(page, "chat")), `one row at ${w} px`).toBe(true);
    }
    // With real room again they come back.
    await setWidth(boundary + 60);
    expect(await words(), "the words come back with room to spare").toBe(true);
  });
});
