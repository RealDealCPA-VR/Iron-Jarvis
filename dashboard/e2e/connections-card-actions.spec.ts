import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * A Connections card's buttons stay inside the card (the user's report of
 * 2026-10-24, a screenshot of Settings › Connections at about 2000 × 1100: on
 * the connected Brave Search card the switch, Test, "Change key" and Remove
 * shared one line that never wrapped, so "Change key" broke onto two lines
 * and Remove ran past the card's edge — "Remo").
 *
 * In a real browser, with GET /connectors stubbed to a connected, keyed
 * Brave Search card (switch + Test + Change key + Remove), a connected
 * keyless card (switch + Test + Remove) and one not connected, at the user's
 * size and four more: every button of a connected card's action row sits
 * wholly inside its card, its words on ONE line, and the page never pans
 * sideways.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const WEB = `http://127.0.0.1:${process.env.IJ_E2E_WEB_PORT || 8808}`;

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

function connector(id: string, name: string, over: Record<string, unknown> = {}) {
  return {
    id,
    name,
    category: "Data & Web",
    glyph: "",
    blurb: `${name} blurb.`,
    unlocks: `${name} unlocks.`,
    connect_via: "mcp",
    scopes: [],
    docs_url: "",
    fields: [],
    provider: "",
    connected: false,
    status: "disconnected",
    tools_loaded: 0,
    tool_names: [],
    needs: "",
    package_note: "",
    href: "",
    action: "",
    ...over,
  };
}

const CONNECTORS = {
  categories: ["Data & Web"],
  connectors: [
    connector("brave", "Brave Search", {
      connected: true,
      status: "connected",
      keyed: true,
      enabled: true,
      tools_loaded: 8,
      can_change: 8,
      asks_first: false,
      held_back: 0,
      tool_names: [
        "brave_image_search", "brave_llm_context", "brave_local_search", "brave_news_search",
        "brave_place_search", "brave_summarizer", "brave_video_search", "brave_web_search",
      ],
      fields: [{ name: "BRAVE_API_KEY", label: "Brave API key", help: "", kind: "secret", optional: false }],
    }),
    connector("sequential_thinking", "Step-by-step reasoning", {
      connected: true,
      status: "connected",
      keyed: false,
      enabled: true,
      tools_loaded: 1,
      can_change: 1,
      asks_first: false,
      held_back: 0,
      tool_names: ["sequentialthinking"],
    }),
    connector("stripe", "Stripe", { category: "Data & Web" }),
  ],
};

async function open(page: Page, width: number, height: number): Promise<void> {
  await page.setViewportSize({ width, height });
  const body = JSON.stringify(CONNECTORS);
  await page.route(`${DAEMON}/connectors`, (route) => stub(route, body));
  await page.goto(`${WEB}/marketplace`);
  await expect(page.getByTestId("connector-actions-brave")).toBeVisible({ timeout: 20_000 });
}

interface Row {
  id: string;
  card: { left: number; right: number };
  buttons: { text: string; left: number; right: number; height: number; lineHeight: number }[];
}

async function rows(page: Page): Promise<{ rows: Row[]; pan: number }> {
  return page.evaluate(() => {
    const out: Row[] = [];
    for (const row of Array.from(document.querySelectorAll<HTMLElement>("[data-testid^='connector-actions-']"))) {
      const card = row.closest<HTMLElement>("[data-testid^='connector-card-']");
      if (!card) continue;
      const c = card.getBoundingClientRect();
      const buttons = Array.from(row.querySelectorAll<HTMLElement>("button")).map((b) => {
        const r = b.getBoundingClientRect();
        const lh = parseFloat(getComputedStyle(b).lineHeight) || 16;
        return { text: (b.textContent || "").trim(), left: r.left, right: r.right, height: r.height, lineHeight: lh };
      });
      out.push({ id: row.dataset.testid || "", card: { left: c.left, right: c.right }, buttons });
    }
    return { rows: out, pan: document.documentElement.scrollWidth - window.innerWidth };
  });
}

for (const { width, height } of SIZES) {
  test(`a connected card's buttons stay inside it at ${width}×${height}`, async ({ page }) => {
    await open(page, width, height);
    const { rows: found, pan } = await rows(page);
    expect(pan, "the page pans sideways").toBeLessThanOrEqual(0);
    expect(found.map((r) => r.id).sort()).toEqual(["connector-actions-brave", "connector-actions-sequential_thinking"]);
    for (const row of found) {
      const words = row.buttons.map((b) => b.text).join(" | ");
      expect(words, row.id).toContain("Remove");
      for (const b of row.buttons) {
        expect(b.left, `${row.id} "${b.text}" starts outside its card`).toBeGreaterThanOrEqual(row.card.left - 0.5);
        expect(b.right, `${row.id} "${b.text}" runs past its card's edge`).toBeLessThanOrEqual(row.card.right + 0.5);
        // One line of words: a button's height stays under two lines' worth
        // (its padding included), so "Change key" never breaks in two.
        if (b.text) expect(b.height, `${row.id} "${b.text}" breaks onto two lines`).toBeLessThan(b.lineHeight * 2 + 4);
      }
    }
    const brave = found.find((r) => r.id === "connector-actions-brave");
    expect(brave?.buttons.map((b) => b.text)).toEqual(expect.arrayContaining(["Test", "Change key", "Remove"]));
  });
}
