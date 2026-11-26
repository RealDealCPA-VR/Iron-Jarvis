import { expect, test, type Route } from "@playwright/test";

/**
 * Next leaps 13-b (build-out plan R2.10, guardrail G11): the rows under a
 * reply that looked up earlier work fit a phone and a desktop.
 *
 * In a real browser, with GET /chat/threads/t9 stubbed to a saved chat whose
 * reply carries three rows (one with a long title, files and both quiet
 * links), at 390 × 844 and three desktop sizes: the page never pans
 * sideways, every row sits inside the reply's column, each row's title is
 * cut on one line rather than pushing Files and Decisions out, and those two
 * links are each on one line inside the window.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const WEB = `http://127.0.0.1:${process.env.IJ_E2E_WEB_PORT || 8808}`;

const SIZES = [
  { width: 390, height: 844 },
  { width: 1024, height: 640 },
  { width: 1440, height: 900 },
  { width: 2000, height: 1103 },
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

const ROWS = [
  {
    id: "chat:t3", kind: "chat",
    title: "Bank feed review for the whole of the first quarter, with every account matched again line by line",
    when: "2026-03-29T15:00:00+00:00", status: "still open", open: 1, link: "/chat?thread=t3",
    files: ["q1-bank-feed-reconciliation-final-version.xlsx", "notes.docx"],
    paths: ["D:\\Clients\\Baker\\q1-bank-feed-reconciliation-final-version.xlsx", "D:\\Clients\\Baker\\notes.docx"],
    files_total: 2, decisions: "/clients/client_00000000000d", times: 1,
  },
  {
    id: "chat:t2", kind: "chat", title: "Baker sales tax filing", when: "2026-03-17T15:00:00+00:00",
    status: "", open: 0, link: "/chat?thread=t2", files: ["march.xlsx"], paths: ["D:\\Clients\\Baker\\march.xlsx"],
    files_total: 1, decisions: "", times: 1,
  },
  {
    id: "run:s1", kind: "run", title: "Baker payroll setup", when: "2026-03-04T15:00:00+00:00",
    status: "needs you", open: 0, link: "/sessions/s1", files: [], paths: [], files_total: 0,
    decisions: "/memory?work=run%3As1", times: 3,
  },
];

const THREAD = JSON.stringify({
  id: "t9",
  title: "Baker in March",
  updated_at: "2026-11-26T10:00:00+00:00",
  persona: null,
  project_id: null,
  messages: [
    { role: "user", content: "what did we do for Baker in March?" },
    { role: "assistant", content: "Three pieces of work in March, for Baker.", workRows: ROWS },
  ],
  setup: {},
  derived_documents: [],
});

for (const { width, height } of SIZES) {
  test(`the rows under a reply fit at ${width}×${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.route(`${DAEMON}/chat/threads/t9**`, (route) => stub(route, THREAD));
    await page.goto(`${WEB}/chat?thread=t9`);
    const block = page.getByTestId("work-history-rows");
    await expect(block).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("work-history-row")).toHaveCount(3);
    const m = await page.evaluate(() => {
      const box = document.querySelector<HTMLElement>("[data-testid='work-history-rows']")!;
      const b = box.getBoundingClientRect();
      const rows = Array.from(box.querySelectorAll<HTMLElement>("[data-testid='work-history-row']")).map((row) => {
        const r = row.getBoundingClientRect();
        const links = Array.from(row.querySelectorAll<HTMLElement>(
          "[data-testid='work-history-files'], [data-testid='work-history-decisions']",
        )).map((el) => {
          const e = el.getBoundingClientRect();
          const lh = parseFloat(getComputedStyle(el).lineHeight) || 18;
          return { text: (el.textContent || "").trim(), left: e.left, right: e.right, height: e.height, lh };
        });
        const title = row.querySelector<HTMLElement>("[data-testid='work-history-open'] span");
        return {
          left: r.left, right: r.right, links,
          titleCut: title ? title.scrollWidth > title.clientWidth + 1 : false,
          titleRight: title ? title.getBoundingClientRect().right : 0,
        };
      });
      return { pan: document.documentElement.scrollWidth - window.innerWidth, box: { left: b.left, right: b.right }, rows, vw: window.innerWidth };
    });
    expect(m.pan, "the page pans sideways").toBeLessThanOrEqual(0);
    for (const [i, row] of m.rows.entries()) {
      expect(row.left, `row ${i} starts inside its column`).toBeGreaterThanOrEqual(m.box.left - 1);
      expect(row.right, `row ${i} ends inside its column`).toBeLessThanOrEqual(m.box.right + 1);
      expect(row.titleRight, `row ${i}'s title stays inside the window`).toBeLessThanOrEqual(m.vw);
      for (const link of row.links) {
        expect(link.right, `${link.text} inside the window`).toBeLessThanOrEqual(m.vw);
        expect(link.left, `${link.text} inside the window`).toBeGreaterThanOrEqual(0);
        expect(link.height, `${link.text} on one line`).toBeLessThanOrEqual(link.lh * 1.5);
      }
    }
    // The long title is cut on its line at a phone's width, never wrapped over Files.
    if (width === 390) expect(m.rows[0].titleCut).toBe(true);
  });
}
