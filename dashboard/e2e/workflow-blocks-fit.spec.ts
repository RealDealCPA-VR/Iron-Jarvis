import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Next leaps 09-a (build-out plan R2.20, guardrail G11): a workflow with a
 * For each and an If fits a phone and a desktop, in chat and on the canvas.
 *
 * In a real browser, with GET /chat/threads/t09a stubbed to a saved chat whose
 * reply carries a workflow draft (a For each over a folder holding a tool
 * step and an If whose lanes hold an ask and an Agent step, then a message),
 * at 390 × 844 and 1440 × 900:
 *
 *   - the draft card shows the block's steps and the per-item cost line, sits
 *     inside the window, and the page never pans sideways;
 *   - "Open in editor" takes the draft to Workflows, where the For each and
 *     the If are boxes with their steps inside, the page never pans
 *     sideways, and selecting the If opens its three-field panel inside the
 *     window.
 *
 * A screenshot per case goes to IJ_BLOCKS_FIT_OUT when set.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const WEB = `http://127.0.0.1:${process.env.IJ_E2E_WEB_PORT || 8808}`;
const OUT = process.env.IJ_BLOCKS_FIT_OUT || "";

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

const leaf = (name: string, over: Record<string, unknown> = {}) => ({
  name,
  agent: "builder",
  task: name,
  tool: null,
  kind: "agent",
  ...over,
});

const DRAFT = {
  name: "Intake totals over 5,000",
  description: "For each PDF in Intake, read the total; over 5,000 ask me, otherwise file it.",
  steps: [
    {
      ...leaf("Each PDF in Intake"),
      kind: "for_each",
      over: { files: { folder: "C:/Users/me/Documents/Intake for the whole of the first quarter", glob: "invoice-*.pdf" } },
      steps: [
        leaf("Read total", { kind: "tool", tool: "read_document", args: { path: "{{item}}" } }),
        {
          ...leaf("Over 5,000?"),
          kind: "if",
          condition: { left: "{{Read total.data.total}}", op: "over", right: "5,000" },
          steps: [leaf("Ask me", { kind: "ask", message: "{{item.name}} totals {{Read total.data.total}}. File it?" })],
          else_steps: [leaf("File it", { task: "File {{item}} in the archive folder for this quarter" })],
        },
      ],
    },
    leaf("Tell me", { kind: "notify", message: "Intake: {{Each PDF in Intake}}" }),
  ],
};

const THREAD = JSON.stringify({
  id: "t09a",
  title: "Intake totals",
  updated_at: "2026-11-27T10:00:00+00:00",
  persona: null,
  project_id: null,
  messages: [
    { role: "user", content: "For each PDF in Intake, read the total. If it is over 5,000, ask me. Otherwise file it." },
    { role: "assistant", content: "Here is that workflow. Save it, run it once, or open it in the editor.", workflowDraft: DRAFT },
  ],
  setup: {},
  derived_documents: [],
});

async function pan(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

async function shot(page: Page, name: string): Promise<void> {
  if (!OUT) return;
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: join(OUT, `${name}.png`) });
}

for (const { width, height } of SIZES) {
  test(`a For each and an If fit at ${width}×${height}, in chat and on the canvas`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.route(`${DAEMON}/chat/threads/t09a**`, (route) => stub(route, THREAD));
    await page.goto(`${WEB}/chat?thread=t09a`);

    // The chat's draft card.
    const open = page.getByRole("button", { name: "Open in editor" });
    await expect(open).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("draft-block-steps").first()).toBeVisible();
    await expect(page.getByTestId("draft-each-cost")).toBeVisible();
    await expect(page.getByText("Read total").first()).toBeVisible();
    const card = await page.evaluate(() => {
      const cost = document.querySelector<HTMLElement>("[data-testid='draft-each-cost']")!;
      const r = cost.getBoundingClientRect();
      const blocks = Array.from(document.querySelectorAll<HTMLElement>("[data-testid='draft-block-steps']")).map((b) => {
        const x = b.getBoundingClientRect();
        return { left: x.left, right: x.right };
      });
      return { cost: { left: r.left, right: r.right }, blocks, vw: window.innerWidth };
    });
    expect(await pan(page), "the chat pans sideways").toBeLessThanOrEqual(0);
    expect(card.cost.right, "the cost line inside the window").toBeLessThanOrEqual(card.vw);
    expect(card.cost.left, "the cost line inside the window").toBeGreaterThanOrEqual(0);
    for (const [i, b] of card.blocks.entries()) {
      expect(b.right, `block list ${i} inside the window`).toBeLessThanOrEqual(card.vw);
      expect(b.left, `block list ${i} inside the window`).toBeGreaterThanOrEqual(0);
    }
    await shot(page, `chat-card-${width}`);

    // Open in editor: the canvas.
    await open.click();
    await page.waitForURL(/\/workflows/);
    await expect(page.getByTestId("each-node")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("if-node")).toBeVisible();
    await expect(page.getByTestId("if-lane-then")).toBeVisible();
    await expect(page.getByTestId("if-lane-else")).toBeVisible();
    expect(await pan(page), "Workflows pans sideways").toBeLessThanOrEqual(0);
    await shot(page, `canvas-${width}`);

    // Selecting the If opens its panel (the value, the comparison, what to compare it with).
    await page.getByTestId("if-node").click({ position: { x: 12, y: 8 } });
    const panel = page.getByText("Over 5,000?").last();
    await expect(panel).toBeVisible();
    expect(await pan(page), "Workflows pans sideways with the If selected").toBeLessThanOrEqual(0);
    const inputs = await page.evaluate(() =>
      Array.from(document.querySelectorAll<HTMLElement>("input, select"))
        .filter((el) => el.offsetParent !== null)
        .map((el) => {
          const r = el.getBoundingClientRect();
          return { left: r.left, right: r.right, w: r.width };
        }),
    );
    for (const [i, el] of inputs.entries()) {
      if (el.w === 0) continue;
      expect(el.right, `field ${i} inside the window`).toBeLessThanOrEqual(width + 1);
    }
    await shot(page, `canvas-if-selected-${width}`);
    if (OUT) writeFileSync(join(OUT, `fields-${width}.json`), JSON.stringify(inputs, null, 1));
  });
}
