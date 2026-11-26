import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Next leaps 16-a (build-out plan R2.11, guardrail G11): search by meaning's
 * card under a reply and its plain settings row fit a phone and a desktop.
 *
 * In a real browser, with GET /memory/meaning stubbed (the page never asks
 * the scratch daemon, so nothing probes a model server on this PC) to a view
 * with a long model name and two models, and GET /chat/threads/t9 stubbed to
 * a saved chat whose reply carries the offer: at 390 × 844 and 1440 × 900 the
 * page never pans sideways, the card and the row sit inside their column,
 * and every press is inside the window, also with the model list open.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const WEB = `http://127.0.0.1:${process.env.IJ_E2E_WEB_PORT || 8808}`;

const SIZES = [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
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
  if (route.request().method() !== "GET") {
    await route.fulfill({ status: 200, headers: { ...CORS, "content-type": "application/json" }, body: "{}" });
    return;
  }
  await route.fulfill({ status: 200, headers: { ...CORS, "content-type": "application/json" }, body });
}

const LONG = "hf.co/someone/a-very-long-multilingual-sentence-embedding-model-name-GGUF:Q8_0";
const CHOICE = `ollama:${LONG} @ http://127.0.0.1:11434`;
const VIEW = JSON.stringify({
  state: "offered", value: null, on: false, words: "Search by meaning: off. It matches words for now.", offer: true,
  best: {
    choice: CHOICE, model: LONG, server: "Ollama on this PC", where_words: "on this PC", this_pc: true,
    cost_words: "Uses about 300 MB of memory while it works. Already on your disk.", works: true,
  },
  candidates: [
    {
      choice: CHOICE, model: LONG, server: "Ollama on this PC", where_words: "on this PC", this_pc: true,
      cost_words: "Uses about 300 MB of memory while it works. Already on your disk.", works: true,
    },
    {
      choice: "openai:text-embedding-nomic-embed-text-v1.5 @ http://192.168.1.40:1234",
      model: "text-embedding-nomic-embed-text-v1.5", server: "Studio PC in the back office",
      where_words: "on Studio PC in the back office (your network)", this_pc: false,
      cost_words: "Not measured: models like this usually need about their file size in memory (about 261 MB).",
      works: null,
    },
  ],
  looking: false, none_found: "No search model was found on this PC or your network.",
  terminal_line: "ollama pull nomic-embed-text",
});

const THREAD = JSON.stringify({
  id: "t9",
  title: "The angry email",
  updated_at: "2026-11-26T10:00:00+00:00",
  persona: null,
  project_id: null,
  messages: [
    { role: "user", content: "find the angry email about shipping" },
    { role: "assistant", content: "I searched your folders by their words.", meaningOffer: true },
  ],
  setup: {},
  derived_documents: [],
});

async function fits(page: Page, testId: string): Promise<void> {
  const m = await page.evaluate((id) => {
    const box = document.querySelector<HTMLElement>(`[data-testid='${id}']`)!;
    const b = box.getBoundingClientRect();
    const presses = Array.from(box.querySelectorAll<HTMLElement>("button, a")).map((el) => {
      const e = el.getBoundingClientRect();
      return { text: (el.textContent || "").trim(), left: e.left, right: e.right };
    });
    return {
      pan: document.documentElement.scrollWidth - window.innerWidth,
      vw: window.innerWidth,
      box: { left: b.left, right: b.right, over: box.scrollWidth - box.clientWidth },
      presses,
    };
  }, testId);
  expect(m.pan, "the page pans sideways").toBeLessThanOrEqual(0);
  expect(m.box.left, `${testId} starts inside the window`).toBeGreaterThanOrEqual(0);
  expect(m.box.right, `${testId} ends inside the window`).toBeLessThanOrEqual(m.vw);
  expect(m.box.over, `${testId}'s words overflow it`).toBeLessThanOrEqual(1);
  for (const p of m.presses) {
    expect(p.left, `${p.text} inside the window`).toBeGreaterThanOrEqual(0);
    expect(p.right, `${p.text} inside the window`).toBeLessThanOrEqual(m.vw);
  }
}

for (const { width, height } of SIZES) {
  test(`the card under a reply fits at ${width}×${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.route(`${DAEMON}/memory/meaning**`, (route) => stub(route, VIEW));
    await page.route(`${DAEMON}/chat/threads/t9**`, (route) => stub(route, THREAD));
    await page.goto(`${WEB}/chat?thread=t9`);
    const card = page.getByTestId("meaning-offer");
    await expect(card).toBeVisible({ timeout: 60_000 });
    await expect(card).toContainText("Uses about 300 MB of memory while it works.");
    await fits(page, "meaning-offer");
    await page.getByTestId("meaning-offer-another").click();
    await expect(page.getByTestId("meaning-model")).toHaveCount(2);
    await fits(page, "meaning-offer");
  });

  test(`the plain row on File Search fits at ${width}×${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.route(`${DAEMON}/memory/meaning**`, (route) => stub(route, VIEW));
    await page.goto(`${WEB}/filesearch`);
    const row = page.getByTestId("meaning-row");
    await expect(row).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("meaning-offer")).toHaveCount(0);
    await page.getByTestId("meaning-choose").click();
    await expect(page.getByTestId("meaning-model")).toHaveCount(2);
    await fits(page, "meaning-row");
  });

  test(`the plain row in Settings fits at ${width}×${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.route(`${DAEMON}/memory/meaning**`, (route) => stub(route, VIEW));
    await page.goto(`${WEB}/settings?focus=meaning_search`);
    const row = page.getByTestId("meaning-row");
    await expect(row).toBeVisible({ timeout: 60_000 });
    await page.getByTestId("meaning-choose").click();
    await expect(page.getByTestId("meaning-model")).toHaveCount(2);
    await fits(page, "meaning-row");
  });
}
